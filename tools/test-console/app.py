"""M4Trust local test console.

Gercek pipeline'lari (MinIO indirme, GPT-5.4/mini, Roboflow) tarayicidan
tetikleyip sonucu gorsel olarak gostermek icin. Production servisinin bir
parcasi DEGIL -- contracts/'a veya app/'a hicbir etkisi yok, sadece
app.pipeline.* modullerini dogrudan, RabbitMQ'ya hic girmeden cagirir.

Calistirma (repo kokunden):
  .venv\\Scripts\\python.exe tools/test-console/app.py
"""
from __future__ import annotations

import base64
import hashlib
import io
import mimetypes
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.client import Config as BotoConfig
from dotenv import dotenv_values
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[2]
os.chdir(REPO_ROOT)
sys.path.insert(0, str(REPO_ROOT))

from app.config import get_settings  # noqa: E402
from app.contracts.errors import PipelineFailure  # noqa: E402
from app.pipeline.document_extraction import pipeline as document_extraction_pipeline  # noqa: E402
from app.pipeline.video_analysis import pipeline as video_analysis_pipeline  # noqa: E402
from app.pipeline.video_analysis import roboflow_client  # noqa: E402

app = FastAPI(title="M4Trust Test Console")

_ENV = dotenv_values(REPO_ROOT / ".env")
_BUCKET = "test-console-uploads"

# Sunum gunu icin: her calistirmayi hafizada tutar (server process'i ayakta oldugu surece).
# Internet/API kesintisinde bile onceki gercek sonuclari tekrar gosterebilmek icindir --
# gercek API cagrisi gerektirmez, sadece daha once uretilmis HTML govdesini yeniden sunar.
_HISTORY: list[dict] = []
_HISTORY_BODIES: dict[str, str] = {}

_PAGE_CSS = """
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem;
         background: #0b0f14; color: #e6edf3; }
  h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; color: #8bd0ff; }
  .card { background: #11161c; border: 1px solid #232a33; border-radius: 10px; padding: 1.2rem 1.4rem; margin: 1rem 0; }
  form.card label { display: block; margin: 0.6rem 0 0.2rem; font-size: 0.9rem; color: #9fb0c0; }
  input[type=file] { color: inherit; }
  button { margin-top: 1rem; background: #2563eb; color: white; border: none; border-radius: 6px;
           padding: 0.55rem 1.1rem; font-size: 0.95rem; cursor: pointer; }
  button:hover { background: #1d4ed8; }
  .warn { color: #f59e0b; font-size: 0.85rem; }
  table { border-collapse: collapse; width: 100%; margin: 0.5rem 0; }
  th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid #232a33; font-size: 0.9rem; }
  th { color: #9fb0c0; font-weight: 600; }
  .badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 999px; font-size: 0.75rem; }
  .badge.ok { background: #14532d; color: #86efac; }
  .badge.warn { background: #78350f; color: #fdba74; }
  .badge.err { background: #7f1d1d; color: #fca5a5; }
  .badge.info { background: #1e3a5f; color: #93c5fd; }
  a.back { color: #8bd0ff; text-decoration: none; font-size: 0.9rem; }
  a.navlink { color: #8bd0ff; text-decoration: none; font-size: 0.9rem; margin-right: 1rem; }
  img.result { max-width: 100%; border-radius: 8px; margin-top: 0.6rem; border: 1px solid #232a33; }
  pre { background: #05080b; padding: 0.8rem; border-radius: 6px; overflow-x: auto; font-size: 0.8rem; }
</style>
"""


def _page(title: str, body: str) -> HTMLResponse:
    nav = ("<p><a class='navlink' href='/'>🏠 Ana sayfa</a>"
           "<a class='navlink' href='/history'>🕘 Geçmiş</a></p>")
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>{_PAGE_CSS}"
                         f"</head><body><h1>M4Trust Test Console</h1>{nav}{body}</body></html>")


def _minio_client():
    return boto3.client(
        "s3",
        endpoint_url=_ENV["OBJECT_STORAGE_ENDPOINT"],
        aws_access_key_id=_ENV["OBJECT_STORAGE_ACCESS_KEY"],
        aws_secret_access_key=_ENV["OBJECT_STORAGE_SECRET_KEY"],
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _ensure_bucket(client) -> None:
    try:
        client.head_bucket(Bucket=_BUCKET)
    except Exception:
        client.create_bucket(Bucket=_BUCKET)


def _upload_and_presign(data: bytes, filename: str) -> tuple[str, str]:
    client = _minio_client()
    _ensure_bucket(client)
    key = f"{uuid.uuid4()}-{filename}"
    client.put_object(Bucket=_BUCKET, Key=key, Body=data)
    url = client.generate_presigned_url("get_object", Params={"Bucket": _BUCKET, "Key": key}, ExpiresIn=900)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return url, expires_at


def _ts(minutes: int = 15) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _envelope(job_type: str, input_payload: dict, processing: dict) -> dict:
    event_id, correlation_id, job_id, tenant_id, transaction_id, subject_id = (str(uuid.uuid4()) for _ in range(6))
    return {
        "eventId": event_id,
        "eventType": "ai.job.requested.v1",
        "schemaVersion": "1.0.0",
        "occurredAt": _ts(minutes=0),
        "correlationId": correlation_id,
        "causationId": None,
        "jobId": job_id,
        "jobType": job_type,
        "tenantId": tenant_id,
        "transactionId": transaction_id,
        "subjectId": subject_id,
        "idempotencyKey": f"test-console-{job_id}",
        "producer": {"service": "m4trust-core-api", "version": "test-console"},
        "payload": {"input": input_payload, "processing": processing, "deadlineAt": _ts(minutes=15)},
    }


def _guess_media_type(filename: str, declared: str | None) -> str:
    if declared and declared != "application/octet-stream":
        return declared
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _build_document_request(data: bytes, filename: str, content_type: str | None) -> dict:
    url, expires_at = _upload_and_presign(data, filename)
    request = _envelope(
        "DOCUMENT_EXTRACTION",
        {
            "fileName": filename,
            "mediaType": _guess_media_type(filename, content_type),
            "sizeBytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "download": {"url": url, "expiresAt": expires_at},
        },
        {
            "languageHints": ["tr", "en"],
            "documentCategory": "B2B_CONTRACT",
            "requestedOutputSchema": "m4trust.document-extraction-result",
            "requestedOutputSchemaVersion": "1.0.0",
            "privacyProfile": "DEFAULT",
            "retrievalProfile": "M4TRUST_LEGAL_DEFAULT",
        },
    )
    request["payload"]["input"]["documentId"] = request["subjectId"]
    return request


def _build_video_request(data: bytes, filename: str, content_type: str | None) -> dict:
    url, expires_at = _upload_and_presign(data, filename)
    request = _envelope(
        "VIDEO_ANALYSIS",
        {
            "fileName": filename,
            "mediaType": _guess_media_type(filename, content_type or "video/mp4"),
            "sizeBytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "download": {"url": url, "expiresAt": expires_at},
        },
        {
            "analysisProfile": "DELIVERY_EVIDENCE_DEFAULT",
            "expectedObjects": [],
            "requestedOutputSchema": "m4trust.video-analysis-result",
            "requestedOutputSchemaVersion": "1.0.0",
        },
    )
    request["payload"]["input"]["videoId"] = request["subjectId"]
    return request


def _record_history(kind: str, filename: str, status: str, summary: str, body: str) -> str:
    """Bir calistirmayi hafizadaki gecmise kaydeder, /history/<id> ile tekrar API cagrisi
    yapmadan goruntulenebilir hale getirir (sunum sirasinda internet kesilse bile isler)."""
    run_id = uuid.uuid4().hex[:10]
    _HISTORY_BODIES[run_id] = body
    _HISTORY.insert(0, {
        "id": run_id,
        "at": datetime.now().strftime("%H:%M:%S"),
        "kind": kind,
        "filename": filename,
        "status": status,
        "summary": summary,
    })
    return run_id


def _error_body(exc: PipelineFailure) -> str:
    return f"""
    <div class="card">
      <h2>❌ Pipeline hatası</h2>
      <p><span class="badge err">{exc.code.value}</span> <span class="badge info">{exc.category.value}</span></p>
      <p>{exc.message}</p>
      <pre>{exc.details or {}}</pre>
    </div>"""


def _format_structured_value(sv: dict) -> str:
    t = sv.get("type")
    if t == "MONEY":
        return f"{sv['amountMinor'] / 100:,.2f} {sv['currency']}"
    if t == "PERCENTAGE":
        return f"%{sv['basisPoints'] / 100:.2f}"
    if t == "DURATION":
        return f"{sv['valueSeconds'] / 86400:g} gün"
    if t == "DATE":
        return sv["value"]
    if t == "BOOLEAN":
        return "Evet" if sv["value"] else "Hayır"
    if t == "QUANTITY":
        return f"{sv['value']} {sv['unit']}"
    if t == "TEXT":
        return sv["value"]
    return str(sv)


def _document_result_body(event: dict) -> tuple[str, str]:
    """(body_html, ozet_metni) dondurur -- ozet gecmis tablosunda kullanilir."""
    result = event["payload"]["result"]
    meta = event["payload"]["technicalMetadata"]
    warnings = event["payload"]["warnings"]
    legal_count = sum(1 for r in result["rules"] if "legalBasis" in r)

    parties_rows = "".join(
        f"<tr><td>{p['role']}</td><td>{p['legalName']['value']}</td>"
        f"<td>{p['legalName']['confidence']:.2f}</td></tr>"
        for p in result["parties"]
    ) or "<tr><td colspan='3'><em>parti bulunamadı</em></td></tr>"

    rule_cards = ""
    for r in result["rules"]:
        legal = ""
        if "legalBasis" in r:
            lb = r["legalBasis"]
            legal = f"<span class='badge info'>📖 {lb['source']} m.{lb['articleNo']}</span>"
        rule_cards += f"""
        <div class="card">
          <p><span class="badge ok">{r['category']}</span> {legal}
             <span class="badge info">güven {r['confidence']:.2f}</span></p>
          <p><strong>{r['title']}</strong></p>
          <p>{r['description']}</p>
          <p>Değer: <strong>{_format_structured_value(r['structuredValue'])}</strong></p>
        </div>"""
    rule_cards = rule_cards or "<p><em>kural bulunamadı</em></p>"

    warning_rows = "".join(
        f"<tr><td><span class='badge warn'>{w['severity']}</span></td><td>{w['code']}</td><td>{w['message']}</td></tr>"
        for w in warnings
    ) or "<tr><td colspan='3'><em>uyarı yok</em></td></tr>"

    body = f"""
    <div class="card">
      <h2>✅ Doküman çıkarımı tamamlandı</h2>
      <p><span class="badge info">model {meta['modelVersion']}</span>
         <span class="badge info">{meta['durationMs']} ms</span>
         <span class="badge info">{result['document']['detectedLanguage']}, {result['document']['pageCount']} sayfa,
         {result['document']['textExtractionMethod']}</span></p>
    </div>
    <h2>Taraflar</h2>
    <div class="card"><table><tr><th>Rol</th><th>Ad</th><th>Güven</th></tr>{parties_rows}</table></div>
    <h2>Kurallar</h2>
    {rule_cards}
    <h2>Uyarılar</h2>
    <div class="card"><table><tr><th>Önem</th><th>Kod</th><th>Mesaj</th></tr>{warning_rows}</table></div>
    """
    summary = f"{len(result['parties'])} taraf, {len(result['rules'])} kural ({legal_count} legalBasis'li), {len(warnings)} uyarı, {meta['durationMs']}ms"
    return body, summary


def _draw_predictions(img: Image.Image, predictions: list[dict], color: str) -> None:
    draw = ImageDraw.Draw(img)
    for p in predictions:
        x, y, w, h = p.get("x", 0), p.get("y", 0), p.get("width", 0), p.get("height", 0)
        left, top, right, bottom = x - w / 2, y - h / 2, x + w / 2, y + h / 2
        draw.rectangle([left, top, right, bottom], outline=color, width=3)
        label = f"{p.get('class', '?')} {p.get('confidence', 0):.2f}"
        text_top = max(0, top - 18)
        draw.rectangle([left, text_top, left + len(label) * 7 + 6, text_top + 16], fill=color)
        draw.text((left + 3, text_top + 1), label, fill="black")


def _predictions_table(predictions: list[dict], empty_message: str) -> str:
    rows = "".join(
        f"<tr><td>{p.get('class')}</td><td>{p.get('confidence', 0):.2f}</td>"
        f"<td>{p.get('x', 0):.0f},{p.get('y', 0):.0f}</td><td>{p.get('width', 0):.0f}x{p.get('height', 0):.0f}</td></tr>"
        for p in predictions
    ) or f"<tr><td colspan='4'><em>{empty_message}</em></td></tr>"
    return f"<table><tr><th>Sınıf</th><th>Güven</th><th>Merkez (x,y)</th><th>Boyut (wxh)</th></tr>{rows}</table>"


def _photo_result_body(image_b64: str, logistics: list[dict], damage: list[dict], settings) -> tuple[str, str]:
    body = f"""
    <div class="card">
      <h2>✅ Roboflow tespiti tamamlandı</h2>
      <p><span class="badge ok">■ lojistik/sayım ({settings.roboflow_logistics_model_id})</span>
         <span class="badge err">■ hasar ({settings.roboflow_damage_model_id})</span></p>
      <img class="result" src="data:image/jpeg;base64,{image_b64}" />
    </div>
    <h2>Lojistik/sayım tespitleri ({len(logistics)})</h2>
    <div class="card">{_predictions_table(logistics, "tespit yok")}</div>
    <h2>Hasar tespitleri ({len(damage)})</h2>
    <div class="card">{_predictions_table(damage, "tespit yok")}</div>
    """
    summary = f"{len(logistics)} lojistik tespiti, {len(damage)} hasar tespiti"
    return body, summary


def _video_result_body(event: dict) -> tuple[str, str]:
    result = event["payload"]["result"]
    meta = event["payload"]["technicalMetadata"]
    summary_obj = result["summary"]

    obs_rows = "".join(
        f"<tr><td>{o['type']}</td><td>{o['label']}</td><td>{o['observedValue']}</td>"
        f"<td>{o['confidence']:.2f}</td><td>{o['timeRange']['startMs']} ms</td></tr>"
        for o in result["observations"]
    ) or "<tr><td colspan='5'><em>gözlem yok</em></td></tr>"

    anomaly_rows = "".join(
        f"<tr><td>{a['type']}</td><td>{a['severity']}</td><td>{a['confidence']:.2f}</td><td>{a['description']}</td></tr>"
        for a in result["anomalies"]
    ) or "<tr><td colspan='4'><em>anomali yok</em></td></tr>"

    outcome_badge = "ok" if summary_obj["advisoryOutcome"] == "NO_ISSUE_DETECTED" else "warn"
    body = f"""
    <div class="card">
      <h2>✅ Video analizi tamamlandı (tam pipeline)</h2>
      <p><span class="badge {outcome_badge}">{summary_obj['advisoryOutcome']}</span>
         <span class="badge info">{meta['durationMs']} ms</span>
         <span class="badge info">{result['durationMs']} ms video</span></p>
      <p>Gözden geçirme nedenleri: {', '.join(summary_obj['reviewReasons']) or '—'}</p>
    </div>
    <h2>Gözlemler</h2>
    <div class="card"><table><tr><th>Tip</th><th>Etiket</th><th>Değer</th><th>Güven</th><th>Zaman</th></tr>{obs_rows}</table></div>
    <h2>Anomaliler</h2>
    <div class="card"><table><tr><th>Tip</th><th>Önem</th><th>Güven</th><th>Açıklama</th></tr>{anomaly_rows}</table></div>
    """
    summary = f"{summary_obj['advisoryOutcome']}, {len(result['observations'])} gözlem, {len(result['anomalies'])} anomali"
    return body, summary


_HOME_BODY = """
<div class="card">
  <p>Bu araç, gerçek pipeline'ları (GPT-5.4/mini / Roboflow) çağırır — her "çalıştır" tıklaması
  gerçek, ücretli bir API çağrısı yapar. Geçmiş sonuçları tekrar API çağrısı yapmadan
  <a class="navlink" href="/history">Geçmiş</a> sayfasından görebilirsin.</p>
</div>

<h2>1) Doküman çıkarımı (PDF/DOCX)</h2>
<form class="card" action="/run/document" method="post" enctype="multipart/form-data">
  <label>Tek sözleşme dosyası</label>
  <input type="file" name="file" accept=".pdf,.docx" required>
  <p class="warn">⚠️ gerçek GPT-5.4/mini çağrısı yapar</p>
  <button type="submit">Çalıştır</button>
</form>
<form class="card" action="/run/document/batch" method="post" enctype="multipart/form-data">
  <label>Toplu test — birden fazla sözleşme seç</label>
  <input type="file" name="files" accept=".pdf,.docx" multiple required>
  <p class="warn">⚠️ her dosya için ayrı gerçek GPT-5.4/mini çağrısı yapar</p>
  <button type="submit">Toplu çalıştır</button>
</form>

<h2>2) Video analizi — fotoğraf ile hızlı test</h2>
<form class="card" action="/run/photo" method="post" enctype="multipart/form-data">
  <label>Tek fotoğraf (JPG/PNG)</label>
  <input type="file" name="file" accept=".jpg,.jpeg,.png" required>
  <p class="warn">⚠️ gerçek Roboflow çağrısı yapar (2 model), sonucu kutucuklarla görselleştirir</p>
  <button type="submit">Çalıştır</button>
</form>
<form class="card" action="/run/photo/batch" method="post" enctype="multipart/form-data">
  <label>Toplu test — birden fazla fotoğraf seç</label>
  <input type="file" name="files" accept=".jpg,.jpeg,.png" multiple required>
  <p class="warn">⚠️ her fotoğraf için ayrı gerçek Roboflow çağrısı yapar (2 model)</p>
  <button type="submit">Toplu çalıştır</button>
</form>

<h2>3) Video analizi — tam pipeline (gerçek video dosyası)</h2>
<form class="card" action="/run/video" method="post" enctype="multipart/form-data">
  <label>Video dosyası (MP4/WebM)</label>
  <input type="file" name="file" accept=".mp4,.webm" required>
  <p class="warn">⚠️ örneklenen her frame için gerçek Roboflow çağrısı yapar</p>
  <button type="submit">Çalıştır</button>
</form>
"""


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    recent = _HISTORY[:5]
    recent_rows = "".join(
        f"<tr><td>{h['at']}</td><td>{h['kind']}</td><td>{h['filename']}</td>"
        f"<td><span class='badge {h['status']}'>{h['status']}</span></td><td>{h['summary']}</td>"
        f"<td><a class='back' href='/history/{h['id']}'>gör</a></td></tr>"
        for h in recent
    )
    recent_block = (
        f"<h2>Son çalıştırmalar</h2><div class='card'><table><tr><th>Saat</th><th>Tür</th><th>Dosya</th>"
        f"<th>Durum</th><th>Özet</th><th></th></tr>{recent_rows}</table></div>"
        if recent else ""
    )
    return _page("M4Trust Test Console", _HOME_BODY + recent_block)


@app.get("/history", response_class=HTMLResponse)
def history() -> HTMLResponse:
    rows = "".join(
        f"<tr><td>{h['at']}</td><td>{h['kind']}</td><td>{h['filename']}</td>"
        f"<td><span class='badge {h['status']}'>{h['status']}</span></td><td>{h['summary']}</td>"
        f"<td><a class='back' href='/history/{h['id']}'>gör</a></td></tr>"
        for h in _HISTORY
    ) or "<tr><td colspan='6'><em>henüz bir şey çalıştırılmadı</em></td></tr>"
    body = f"""
    <div class="card"><p>Bu tablo, server ayakta kaldığı sürece hafızada tutulur (yeniden başlatınca
    sıfırlanır). Bir satıra tıklamak yeni bir API çağrısı YAPMAZ, sadece o çalıştırmanın daha önce
    üretilmiş sonucunu tekrar gösterir — sunum sırasında internet kesilse bile işe yarar.</p></div>
    <div class="card"><table><tr><th>Saat</th><th>Tür</th><th>Dosya</th><th>Durum</th><th>Özet</th><th></th></tr>{rows}</table></div>
    """
    return _page("Geçmiş", body)


@app.get("/history/{run_id}", response_class=HTMLResponse)
def history_detail(run_id: str) -> HTMLResponse:
    body = _HISTORY_BODIES.get(run_id)
    if body is None:
        return _page("Bulunamadı", "<div class='card'><p>Bu kayıt bulunamadı (server yeniden başlamış olabilir).</p></div>")
    return _page("Geçmiş sonuç", body)


@app.post("/run/document", response_class=HTMLResponse)
def run_document(file: UploadFile = File(...)) -> HTMLResponse:
    data = file.file.read()
    filename = file.filename or "upload.pdf"
    request = _build_document_request(data, filename, file.content_type)
    try:
        event = document_extraction_pipeline.run(request)
    except PipelineFailure as exc:
        body = _error_body(exc)
        _record_history("doküman", filename, "err", exc.code.value, body)
        return _page("Hata", body)
    body, summary = _document_result_body(event)
    _record_history("doküman", filename, "ok", summary, body)
    return _page("Doküman sonucu", body)


@app.post("/run/document/batch", response_class=HTMLResponse)
def run_document_batch(files: list[UploadFile] = File(...)) -> HTMLResponse:
    rows = []
    for f in files:
        data = f.file.read()
        filename = f.filename or "upload.pdf"
        request = _build_document_request(data, filename, f.content_type)
        try:
            event = document_extraction_pipeline.run(request)
        except PipelineFailure as exc:
            body = _error_body(exc)
            run_id = _record_history("doküman", filename, "err", exc.code.value, body)
            rows.append((filename, "err", exc.code.value, run_id))
            continue
        body, summary = _document_result_body(event)
        run_id = _record_history("doküman", filename, "ok", summary, body)
        rows.append((filename, "ok", summary, run_id))

    table_rows = "".join(
        f"<tr><td>{name}</td><td><span class='badge {status}'>{status}</span></td>"
        f"<td>{summary}</td><td><a class='back' href='/history/{run_id}'>detay</a></td></tr>"
        for name, status, summary, run_id in rows
    )
    ok_count = sum(1 for _, s, _, _ in rows if s == "ok")
    body = f"""
    <div class="card"><h2>Toplu doküman çıkarımı tamamlandı</h2>
    <p>{ok_count}/{len(rows)} başarılı</p></div>
    <div class="card"><table><tr><th>Dosya</th><th>Durum</th><th>Özet</th><th></th></tr>{table_rows}</table></div>
    """
    return _page("Toplu doküman sonucu", body)


@app.post("/run/photo", response_class=HTMLResponse)
def run_photo(file: UploadFile = File(...)) -> HTMLResponse:
    data = file.file.read()
    filename = file.filename or "upload.jpg"
    settings = get_settings()
    try:
        logistics = roboflow_client.detect_objects(data, settings)
        damage = roboflow_client.detect_damage(data, settings)
    except PipelineFailure as exc:
        body = _error_body(exc)
        _record_history("foto", filename, "err", exc.code.value, body)
        return _page("Hata", body)

    img = Image.open(io.BytesIO(data)).convert("RGB")
    _draw_predictions(img, logistics, "#22c55e")
    _draw_predictions(img, damage, "#ef4444")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    image_b64 = base64.b64encode(buf.getvalue()).decode()
    body, summary = _photo_result_body(image_b64, logistics, damage, settings)
    _record_history("foto", filename, "ok", summary, body)
    return _page("Fotoğraf sonucu", body)


@app.post("/run/photo/batch", response_class=HTMLResponse)
def run_photo_batch(files: list[UploadFile] = File(...)) -> HTMLResponse:
    settings = get_settings()
    rows = []
    for f in files:
        data = f.file.read()
        filename = f.filename or "upload.jpg"
        try:
            logistics = roboflow_client.detect_objects(data, settings)
            damage = roboflow_client.detect_damage(data, settings)
        except PipelineFailure as exc:
            body = _error_body(exc)
            run_id = _record_history("foto", filename, "err", exc.code.value, body)
            rows.append((filename, "err", exc.code.value, run_id))
            continue

        img = Image.open(io.BytesIO(data)).convert("RGB")
        _draw_predictions(img, logistics, "#22c55e")
        _draw_predictions(img, damage, "#ef4444")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        image_b64 = base64.b64encode(buf.getvalue()).decode()
        body, summary = _photo_result_body(image_b64, logistics, damage, settings)
        run_id = _record_history("foto", filename, "ok", summary, body)
        rows.append((filename, "ok", summary, run_id))

    table_rows = "".join(
        f"<tr><td>{name}</td><td><span class='badge {status}'>{status}</span></td>"
        f"<td>{summary}</td><td><a class='back' href='/history/{run_id}'>detay</a></td></tr>"
        for name, status, summary, run_id in rows
    )
    ok_count = sum(1 for _, s, _, _ in rows if s == "ok")
    body = f"""
    <div class="card"><h2>Toplu fotoğraf testi tamamlandı</h2>
    <p>{ok_count}/{len(rows)} başarılı</p></div>
    <div class="card"><table><tr><th>Dosya</th><th>Durum</th><th>Özet</th><th></th></tr>{table_rows}</table></div>
    """
    return _page("Toplu fotoğraf sonucu", body)


@app.post("/run/video", response_class=HTMLResponse)
def run_video(file: UploadFile = File(...)) -> HTMLResponse:
    data = file.file.read()
    filename = file.filename or "upload.mp4"
    request = _build_video_request(data, filename, file.content_type)
    try:
        event = video_analysis_pipeline.run(request)
    except PipelineFailure as exc:
        body = _error_body(exc)
        _record_history("video", filename, "err", exc.code.value, body)
        return _page("Hata", body)
    body, summary = _video_result_body(event)
    _record_history("video", filename, "ok", summary, body)
    return _page("Video sonucu", body)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)

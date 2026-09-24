from __future__ import annotations

from io import BytesIO

from .domain import normalized_to_pdf_rectangle
from .models import DocumentField, EnvelopeDocument, Signature
from .storage import LocalPrivateStorage


def flatten_envelope_document(envelope_document: EnvelopeDocument) -> bytes:
    """Overlay completed responses on a copy of every original PDF page."""
    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
    except ImportError as error:
        raise RuntimeError(
            "pypdf and reportlab are required for completed PDF generation"
        ) from error

    storage = LocalPrivateStorage()
    with storage.open(envelope_document.document.storage_key) as source:
        reader = PdfReader(source)
        writer = PdfWriter()
        fields_by_page: dict[int, list[DocumentField]] = {}
        for field in envelope_document.fields.select_related("recipient").order_by(
            "page_number", "y", "x"
        ):
            fields_by_page.setdefault(field.page_number, []).append(field)
        for page_number, page in enumerate(reader.pages, 1):
            fields = fields_by_page.get(page_number, [])
            if fields:
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
                rotation = int(page.get("/Rotate", 0) or 0)
                overlay_stream = BytesIO()
                overlay = canvas.Canvas(overlay_stream, pagesize=(width, height))
                for field in fields:
                    draw_completed_field(overlay, field, width, height, rotation, storage)
                overlay.save()
                overlay_stream.seek(0)
                page.merge_page(PdfReader(overlay_stream).pages[0])
            writer.add_page(page)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()


def draw_completed_field(
    canvas, field, page_width: float, page_height: float, rotation: int, storage
) -> None:
    rectangle = normalized_to_pdf_rectangle(
        x=float(field.x),
        y=float(field.y),
        width=float(field.width),
        height=float(field.height),
        page_width=page_width,
        page_height=page_height,
        rotation=rotation,
    )
    value = field.value or ""
    if not value and field.field_type != DocumentField.Type.NOTE:
        return
    if field.field_type in (DocumentField.Type.SIGNATURE, DocumentField.Type.INITIAL):
        purpose = (
            Signature.Purpose.SIGNATURE
            if field.field_type == DocumentField.Type.SIGNATURE
            else Signature.Purpose.INITIAL
        )
        signature = (
            field.recipient.signatures.filter(purpose=purpose).order_by("-created_at").first()
        )
        if signature and signature.storage_key:
            from reportlab.lib.utils import ImageReader

            with storage.open(signature.storage_key) as image_file:
                image = ImageReader(image_file)
                canvas.drawImage(
                    image,
                    rectangle.x,
                    rectangle.y,
                    rectangle.width,
                    rectangle.height,
                    preserveAspectRatio=True,
                    anchor="c",
                    mask="auto",
                )
        else:
            draw_fitted_text(
                canvas,
                signature.text_representation if signature else value,
                rectangle,
                font="Helvetica-Oblique",
            )
    elif field.field_type == DocumentField.Type.CHECKBOX:
        if value.lower() in ("checked", "true", "yes", "1", "on"):
            canvas.setLineWidth(1.8)
            canvas.line(
                rectangle.x + rectangle.width * 0.15,
                rectangle.y + rectangle.height * 0.5,
                rectangle.x + rectangle.width * 0.42,
                rectangle.y + rectangle.height * 0.2,
            )
            canvas.line(
                rectangle.x + rectangle.width * 0.42,
                rectangle.y + rectangle.height * 0.2,
                rectangle.x + rectangle.width * 0.88,
                rectangle.y + rectangle.height * 0.85,
            )
    elif field.field_type == DocumentField.Type.RADIO:
        canvas.circle(
            rectangle.x + rectangle.height / 2,
            rectangle.y + rectangle.height / 2,
            rectangle.height * 0.32,
            stroke=1,
            fill=0,
        )
        canvas.circle(
            rectangle.x + rectangle.height / 2,
            rectangle.y + rectangle.height / 2,
            rectangle.height * 0.18,
            stroke=0,
            fill=1,
        )
        draw_fitted_text(canvas, value, rectangle, x_offset=rectangle.height)
    elif field.field_type != DocumentField.Type.ATTACHMENT:
        draw_fitted_text(canvas, value or field.label, rectangle)


def draw_fitted_text(canvas, value: str, rectangle, *, font="Helvetica", x_offset=0) -> None:
    available_width = max(1, rectangle.width - x_offset - 4)
    size = min(12, rectangle.height * 0.62)
    while size > 5 and canvas.stringWidth(value, font, size) > available_width:
        size -= 0.5
    canvas.setFont(font, size)
    canvas.drawString(
        rectangle.x + 2 + x_offset,
        rectangle.y + max(2, (rectangle.height - size) / 2),
        value[:500],
    )

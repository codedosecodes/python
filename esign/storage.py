from __future__ import annotations

import base64
import binascii
import hashlib
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from django.conf import settings
from django.core.exceptions import ValidationError


@dataclass(frozen=True, slots=True)
class StoredDocument:
    key: str
    byte_size: int
    sha256: str


class LocalPrivateStorage:
    """Private development storage; keys are generated and never public URLs."""

    def __init__(self, root: Path | None = None):
        self.root = Path(root or settings.PRIVATE_STORAGE_ROOT).resolve()

    def save(self, stream: BinaryIO, suffix: str = ".pdf") -> StoredDocument:
        key = f"documents/{uuid.uuid4().hex}{suffix.lower()}"
        destination = self._path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with destination.open("wb") as output:
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                size += len(chunk)
                if size > settings.MAX_DOCUMENT_UPLOAD_BYTES:
                    output.close()
                    destination.unlink(missing_ok=True)
                    raise ValidationError("Document exceeds the upload size limit.")
                digest.update(chunk)
                output.write(chunk)
        return StoredDocument(key, size, digest.hexdigest())

    def save_bytes(self, content: bytes, suffix: str = ".pdf") -> StoredDocument:
        from io import BytesIO

        return self.save(BytesIO(content), suffix)

    def open(self, key: str):
        return self._path(key).open("rb")

    def copy(self, key: str, suffix: str = ".pdf") -> StoredDocument:
        with self.open(key) as source:
            return self.save(source, suffix)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if self.root not in candidate.parents:
            raise ValidationError("Invalid storage key.")
        return candidate


def validate_pdf(upload) -> None:
    if not upload.name.lower().endswith(".pdf"):
        raise ValidationError("Only PDF documents are supported in this workflow.")
    if upload.size <= 0:
        raise ValidationError("The selected document is empty.")
    if upload.size > settings.MAX_DOCUMENT_UPLOAD_BYTES:
        raise ValidationError("Document exceeds the upload size limit.")
    header = upload.read(5)
    upload.seek(0)
    if header != b"%PDF-":
        raise ValidationError("The file is not a valid PDF document.")


def decode_signature_image(
    data_url: str, *, max_bytes: int = 2 * 1024 * 1024
) -> tuple[bytes, str, int, int]:
    """Validate a browser-produced PNG/JPEG data URL and return decoded metadata."""
    if not data_url.startswith("data:image/") or "," not in data_url:
        raise ValidationError("Signature image data is invalid.")
    header, encoded = data_url.split(",", 1)
    mime = header[5:].split(";", 1)[0].lower()
    if mime not in ("image/png", "image/jpeg") or ";base64" not in header:
        raise ValidationError("Signatures must be PNG or JPEG images.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValidationError("Signature image encoding is invalid.") from error
    if not content or len(content) > max_bytes:
        raise ValidationError("Signature image must be smaller than 2 MB.")
    if mime == "image/png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) < 24:
            raise ValidationError("Uploaded signature is not a valid PNG image.")
        width, height = struct.unpack(">II", content[16:24])
        suffix = ".png"
    else:
        if not content.startswith(b"\xff\xd8"):
            raise ValidationError("Uploaded signature is not a valid JPEG image.")
        width, height = _jpeg_dimensions(content)
        suffix = ".jpg"
    if width < 20 or height < 10 or width > 6000 or height > 6000:
        raise ValidationError("Signature image dimensions are outside the allowed range.")
    return content, suffix, width, height


def _jpeg_dimensions(content: bytes) -> tuple[int, int]:
    index = 2
    while index + 9 < len(content):
        if content[index] != 0xFF:
            index += 1
            continue
        marker = content[index + 1]
        index += 2
        if marker in (0xD8, 0xD9):
            continue
        if index + 2 > len(content):
            break
        length = int.from_bytes(content[index : index + 2], "big")
        if marker in range(0xC0, 0xC4) and index + 7 < len(content):
            return int.from_bytes(content[index + 5 : index + 7], "big"), int.from_bytes(
                content[index + 3 : index + 5], "big"
            )
        index += max(length, 2)
    raise ValidationError("Uploaded signature is not a valid JPEG image.")

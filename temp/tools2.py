"""Tools: EXIF metadata extraction, image helpers, and VLM invocation.

Pipeline (per Workflow.txt):
    ImageValidationToolNode -> MetadataDecisionComponent
        YES -> VLMValidationComponent
        NO  -> FakeClaimDetectionComponent
    -> ShouldContinueToolExecutionNode

NOTE: missing EXIF is only a WEAK signal (WhatsApp/screenshots often lose
EXIF). Set STRICT_METADATA_REQUIREMENT = True to exactly match the original
workflow (missing EXIF -> automatic validation False).
"""
import base64
import os

from PIL import Image
from PIL.ExifTags import TAGS

from chains import get_vlm_chain
from schema import ImageValidationOutput

STRICT_METADATA_REQUIREMENT = False


# ---------- EXIF metadata tool ----------


def extract_exif(image_path: str) -> dict:
    image = Image.open(image_path)
    exif_data = image.getexif()
    metadata = {}
    if exif_data:
        for tag_id, value in exif_data.items():
            tag_name = TAGS.get(tag_id, tag_id)
            if isinstance(value, bytes):
                value = value.decode(errors="ignore")
            metadata[str(tag_name)] = str(value)
    else:
        metadata["status"] = "No EXIF data found"
    return metadata


# ---------- MetadataDecisionComponent ----------


def has_metadata(metadata: dict) -> bool:
    """True when real EXIF tags were found (not just the 'status' placeholder)."""
    return bool(metadata) and "status" not in metadata


# ---------- Image helpers ----------


def encode_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ---------- FakeClaimDetectionComponent ----------


def fake_claim_detection() -> dict:
    """Runs when metadata is missing (strict workflow branch)."""
    return {"validation": False, "reason": "Missing metadata — suspicious image claim"}


# ---------- VLMValidationComponent (OpenRouter) ----------


def vlm_validate(image_path: str, claim: str) -> dict:
    image_b64 = encode_image_base64(image_path)
    result = get_vlm_chain().invoke({"claim": claim, "image_b64": image_b64})
    return result.model_dump()


# ---------- ImageValidationToolNode pipeline ----------


def run_image_validation(image_path: str, claim: str) -> dict:
    """load image -> EXIF -> metadata decision -> VLM or fake-claim -> result.

    Raises on hard errors (missing file, unreadable image) so the retry node
    (ShouldContinueToolExecutionNode) can catch and loop.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    metadata = extract_exif(image_path)
    metadata_found = has_metadata(metadata)

    # if metadata_found or not STRICT_METADATA_REQUIREMENT:
    if metadata_found or not STRICT_METADATA_REQUIREMENT:
        vlm_result = vlm_validate(image_path, claim)
        validation = bool(vlm_result.get("validation", False))

        signals = []
        if not metadata_found:
            signals.append("No EXIF metadata (weak signal only)")
        if not vlm_result.get("claim_consistent", validation):
            signals.append("Image evidence may not match the claim")

        output = ImageValidationOutput(
            metadata_found=metadata_found,
            validation=validation,
            reason=(
                vlm_result.get("reason", "")
                or ("Evidence looks consistent" if validation else "Image does not support the claim")
            ),
        )
        return {
            "output": output.model_dump(),
            "metadata": metadata,
            "vlm_result": str(vlm_result),
            "fake_claim_result": "; ".join(signals) if signals else None,
        }

    fake = fake_claim_detection()
    output = ImageValidationOutput(
        metadata_found=False,
        validation=fake["validation"],
        reason=fake["reason"],
    )
    return {
        "output": output.model_dump(),
        "metadata": metadata,
        "vlm_result": None,
        "fake_claim_result": fake["reason"],
    }

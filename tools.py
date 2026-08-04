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

from chains import invoke_vlm_with_fallback
from schema import ImageValidationOutput

STRICT_METADATA_REQUIREMENT = False


def extract_exif(image_path):
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


def has_metadata(metadata):
    return bool(metadata) and "status" not in metadata


def encode_image_base64(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def fake_claim_detection():
    return {"validation": False, "reason": "Missing metadata - suspicious image claim"}


def vlm_validate(image_path, claim):
    image_b64 = encode_image_base64(image_path)
    return invoke_vlm_with_fallback(image_b64, claim)


def run_image_validation(image_path, claim):
    if not os.path.exists(image_path):
        raise FileNotFoundError("Image not found: " + str(image_path))

    metadata = extract_exif(image_path)
    metadata_found = has_metadata(metadata)

    should_run_vlm = metadata_found or ( STRICT_METADATA_REQUIREMENT)

    if should_run_vlm:
        vlm_result = vlm_validate(image_path, claim)
        validation = bool(vlm_result.get("validation", False))

        signals = []
        if not metadata_found:
            signals.append("No EXIF metadata (weak signal only)")
        if not vlm_result.get("claim_consistent", validation):
            signals.append("Image evidence may not match the claim")

        reason_text = vlm_result.get("reason", "")
        if not reason_text:
            if validation:
                reason_text = "Evidence looks consistent"
            else:
                reason_text = "Image does not support the claim"

        output = ImageValidationOutput(
            metadata_found=metadata_found,
            validation=validation,
            reason=reason_text,
        )

        fake_claim_result = None
        if signals:
            fake_claim_result = "; ".join(signals)

        return {
            "output": output.model_dump(),
            "metadata": metadata,
            "vlm_result": str(vlm_result),
            "fake_claim_result": fake_claim_result,
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

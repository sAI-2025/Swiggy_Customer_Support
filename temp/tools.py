"""Tools: EXIF metadata extraction, image helpers, and VLM invocation.

Pipeline (per Workflow.txt + requirement #3):
    ImageValidationToolNode -> MetadataDecisionComponent
        YES (EXIF found)    -> VLMValidationComponent
        NO  (no EXIF found) -> flag as ai_generated_suspected, ask for
                                original photo instead of running VLM
    -> ShouldContinueToolExecutionNode

REQUIREMENT #3 CHANGE: previously, missing EXIF only added a "weak
signal" note while still running the VLM. Now, per explicit instruction,
missing EXIF triggers an immediate customer-facing message asking them to
upload the ORIGINAL photo because "it looks like an AI-generated one" —
the VLM call is skipped entirely for that turn (saves a paid/fallback API
call on an image we're already rejecting).
"""
import os

from chains import invoke_vlm_with_fallback
from schema import ImageValidationOutput

from PIL import Image
from PIL.ExifTags import TAGS

AI_GENERATED_MESSAGE = (
    "Please upload the original image on the product. It looks like an "
    "AI-generated one."
)


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


def run_image_validation(image_path, claim):
    """Returns a dict with keys: output, metadata, vlm_result,
    fake_claim_result, ai_generated_suspected.

    Raises on hard errors (missing file) so the retry node can catch them.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError("Image not found: " + str(image_path))

    metadata = extract_exif(image_path)
    metadata_found = has_metadata(metadata)

    # --- Requirement #3: no EXIF -> suspected AI-generated, skip VLM ---
    if not metadata_found:
        output = ImageValidationOutput(
            metadata_found=False,
            validation=False,
            reason=AI_GENERATED_MESSAGE,
        )
        return {
            "output": output.model_dump(),
            "metadata": metadata,
            "vlm_result": None,
            "fake_claim_result": AI_GENERATED_MESSAGE,
            "ai_generated_suspected": True,
        }

    # --- EXIF found: proceed to VLM validation (DashScope -> OpenRouter) ---
    vlm_result = invoke_vlm_with_fallback(image_path, claim)
    validation = bool(vlm_result.get("validation", False))

    signals = []
    if not vlm_result.get("claim_consistent", validation):
        signals.append("Image evidence may not match the claim")

    reason_text = vlm_result.get("reason", "")
    if not reason_text:
        reason_text = "Evidence looks consistent" if validation else "Image does not support the claim"

    output = ImageValidationOutput(
        metadata_found=True,
        validation=validation,
        reason=reason_text,
    )

    fake_claim_result = "; ".join(signals) if signals else None

    return {
        "output": output.model_dump(),
        "metadata": metadata,
        "vlm_result": str(vlm_result),
        "fake_claim_result": fake_claim_result,
        "ai_generated_suspected": False,
    }

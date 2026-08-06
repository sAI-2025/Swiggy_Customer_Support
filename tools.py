"""Tools: EXIF metadata extraction and VLM invocation.

Pipeline (per architecture spec):
    ImageValidationToolNode:
      1. Load image, extract EXIF.
      2. No EXIF -> ai_generated_suspected=True, SKIP VLM entirely, return.
      3. EXIF found -> call VLM (DashScope primary, OpenRouter fallback).
      4. validation=True  -> refund-approved outcome.
      5. validation=False -> claim-rejected outcome (evidence WAS processed,
         just doesn't support the claim — different from AI-generated).

This module is fixture-only (no routing decisions, no message text) —
graph.py's image_validation_node interprets the returned dict and sets
all state flags (ai_generated_suspected, is_claim_submitted, tool_usage,
refund_approved, conversation_resolved).
"""
import os

from PIL import Image
from PIL.ExifTags import TAGS

from chains import invoke_vlm_with_fallback
from schema import ImageValidationOutput


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


def has_metadata(metadata: dict) -> bool:
    return bool(metadata) and "status" not in metadata


def run_image_validation(image_path: str, claim: str) -> dict:
    """Returns one of three OUTCOME dicts:

    {"outcome": "ai_generated", "metadata": ..., "vlm_result": None}
    {"outcome": "approved",     "metadata": ..., "vlm_result": {...}}
    {"outcome": "rejected",     "metadata": ..., "vlm_result": {...}}

    Raises FileNotFoundError on a bad path so the retry loop in graph.py
    can catch it and increment iteration_count.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    metadata = extract_exif(image_path)
    metadata_found = has_metadata(metadata)

    if not metadata_found:
        return {
            "outcome": "ai_generated",
            "metadata": metadata,
            "vlm_result": None,
            "output": ImageValidationOutput(
                metadata_found=False,
                validation=False,
                reason="No EXIF metadata — treated as AI-generated / non-original image.",
            ).model_dump(),
        }

    vlm_result = invoke_vlm_with_fallback(image_path, claim)
    validation = bool(vlm_result.get("validation", False))

    output = ImageValidationOutput(
        metadata_found=True,
        validation=validation,
        reason=vlm_result.get("reason", ""),
    )

    return {
        "outcome": "approved" if validation else "rejected",
        "metadata": metadata,
        "vlm_result": vlm_result,
        "output": output.model_dump(),
    }

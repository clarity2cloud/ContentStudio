# app/services/gamma_service.py
import httpx
import asyncio
import fitz  # PyMuPDF
from typing import Optional, Dict, Any, List, Union
from app.config import settings
from app.utils.logger import logger

GAMMA_BASE_URL = "https://public-api.gamma.app"


class GammaService:
    def __init__(self):
        self.api_key = settings.GAMMA_API_KEY
        self.enabled = bool(self.api_key)
        if self.enabled:
            logger.info("✅ Gamma AI service initialized")
        else:
            logger.warning("⚠️ GAMMA_API_KEY not set")

    def _headers(self) -> Dict[str, str]:
        return {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _check_enabled(self):
        if not self.enabled:
            raise ValueError("GAMMA_API_KEY not configured")

    async def create_generation(
        self,
        input_text: str,
        text_mode: str = "generate",
        format: str = "presentation",
        theme_id: Optional[str] = None,
        num_cards: Optional[int] = None,
        card_split: str = "auto",
        additional_instructions: Optional[str] = None,
        folder_ids: Optional[List[str]] = None,
        export_as: Optional[str] = None,
        text_options: Optional[Dict[str, Any]] = None,
        image_options: Optional[Dict[str, Any]] = None,
        card_options: Optional[Dict[str, Any]] = None,
        sharing_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._check_enabled()

        payload = {
            "inputText": input_text,
            "textMode": text_mode,
            "format": format,
        }

        if theme_id:
            payload["themeId"] = theme_id
        if num_cards is not None:
            payload["numCards"] = num_cards
        if card_split:
            payload["cardSplit"] = card_split
        if additional_instructions:
            payload["additionalInstructions"] = additional_instructions
        if folder_ids:
            payload["folderIds"] = folder_ids
        if export_as:
            payload["exportAs"] = export_as
        if text_options:
            payload["textOptions"] = text_options
        if image_options:
            payload["imageOptions"] = image_options
        if card_options:
            payload["cardOptions"] = card_options
        if sharing_options:
            payload["sharingOptions"] = sharing_options

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{GAMMA_BASE_URL}/v1.0/generations",
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code not in (200, 201):
                logger.error(f"Gamma error {resp.status_code}: {resp.text}")
                raise Exception(f"Gamma API error: {resp.text}")

            data = resp.json()
            generation_id = data.get("id") or data.get("generationId")
            return {
                "generation_id": generation_id,
                "response": data
            }

    async def list_themes(self) -> List[Dict[str, Any]]:
        self._check_enabled()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GAMMA_BASE_URL}/v1.0/themes",
                headers=self._headers(),
            )
            if resp.status_code != 200:
                logger.error(
                    f"Gamma list themes error {resp.status_code}: {resp.text}")
                return []

            data = resp.json()
            return data.get("themes", [])

    async def get_generation(self, generation_id: str) -> Dict[str, Any]:
        self._check_enabled()
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GAMMA_BASE_URL}/v1.0/generations/{generation_id}",
                headers=self._headers(),
            )
            if resp.status_code != 200:
                logger.error(
                    f"Gamma get generation error {resp.status_code}: {resp.text}")
                raise Exception(
                    f"Gamma API returned {resp.status_code}: {resp.text}")
            data = resp.json()
            if "gammaUrl" not in data and "url" in data:
                data["gammaUrl"] = data["url"]
            return data

    async def get_slide_images(self, generation_id: str) -> List[str]:
        """
        Exhaustively extracts image URLs from a completed Gamma generation.
        Prioritizes official export URLs (PDF) and falls back to JSON extraction.
        """
        data = await self.get_generation(generation_id)
        status = data.get("status")

        if status != "completed":
            logger.info(f"Generation {generation_id} not yet completed")
            return []

        # 1. Prioritize Official Export URLs
        export_url = data.get("exportUrl") or data.get("pdfUrl")
        if export_url and (".pd" in export_url.lower()
                           or "export" in export_url.lower()):
            logger.info(
                f"Found official export URL for {generation_id}: {export_url}")
            return [f"pdf_fallback:{export_url}"]

        # 2. Find all URLs recursively in JSON
        all_urls = self._find_all_urls_recursive(data)

        # Filter for image extensions or asset patterns
        image_extensions = (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".svg",
            "/assets/")
        slide_urls = []
        for url in all_urls:
            low_url = url.lower()
            if any(
                    ext in low_url for ext in image_extensions) and url not in slide_urls:
                # Basic validation: must be a full URL
                if url.startswith("http"):
                    slide_urls.append(url)

        if slide_urls:
            logger.info(
                f"Extracted {len(slide_urls)} slide image(s) via recursive JSON extraction")
            return slide_urls

        # 3. Fallback: Check if there's any hidden export data
        logger.warning(
            f"No images found in JSON for {generation_id}. Data keys: {list(data.keys())}. Attempting PDF fallback...")
        return await self._get_images_via_pdf_fallback(generation_id)

    def _find_all_urls_recursive(self, obj: Union[Dict, List]) -> List[str]:
        urls = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(
                        value, str) and (
                        value.startswith("http") or "/assets/" in value):
                    urls.append(value)
                elif isinstance(value, (dict, list)):
                    urls.extend(self._find_all_urls_recursive(value))
        elif isinstance(obj, list):
            for item in obj:
                urls.extend(self._find_all_urls_recursive(item))
        return urls

    async def _get_images_via_pdf_fallback(
            self, generation_id: str) -> List[str]:
        """
        As requested by user: Separate code to export to PDF and convert to images
        if direct image URLs aren't available.
        """
        try:
            logger.info(f"Starting PDF export for fallback: {generation_id}")
            # 1. Trigger Export
            async with httpx.AsyncClient(timeout=30.0) as client:
                export_resp = await client.post(
                    f"{GAMMA_BASE_URL}/v1.0/generations/{generation_id}/exports",
                    headers=self._headers(),
                    json={"exportType": "pdf"}
                )
                if export_resp.status_code not in (200, 201, 202):
                    logger.error(
                        f"Failed to trigger PDF export: {export_resp.text}")
                    return []

                export_id = export_resp.json().get("id")

                # 2. Poll for Export Completion
                max_attempts = 30
                pdf_url = None
                for i in range(max_attempts):
                    status_resp = await client.get(
                        f"{GAMMA_BASE_URL}/v1.0/exports/{export_id}",
                        headers=self._headers()
                    )
                    status_data = status_resp.json()
                    if status_data.get("status") == "completed":
                        pdf_url = status_data.get("url")
                        break
                    elif status_data.get("status") == "failed":
                        logger.error("PDF Export failed")
                        return []
                    await asyncio.sleep(2)

                if not pdf_url:
                    logger.error("Timed out waiting for PDF export")
                    return []

                # 3. Download PDF and Convert to Images
                # Note: This returns data directly because we need to serve it to user.
                # For consistency with slide_urls, we might need a separate flow for the API.
                # However, the requirement is "easily download".
                # To keep it simple for now, we'll return the PDF URL in a special format
                # or have the API handle the conversion.

                # Update: Since get_slide_images expects URLs, but we have a PDF,
                # we'll return the PDF URL with a prefix to signal the API to
                # handle it.
                return [f"pdf_fallback:{pdf_url}"]

        except Exception as _e:
            logger.exception("PDF fallback failed")
            return []

    async def convert_pdf_to_images(self, pdf_url: str) -> List[bytes]:
        """
        Downloads a PDF and converts each page to a high-quality PNG.
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(pdf_url)
            resp.raise_for_status()
            pdf_data = resp.content

        doc = fitz.open(stream=pdf_data, filetype="pd")
        images = []
        for page in doc:
            pix = page.get_pixmap(
                matrix=fitz.Matrix(
                    2, 2))  # 2x scale for quality
            images.append(pix.tobytes("png"))
        doc.close()
        return images


gamma_service = GammaService()

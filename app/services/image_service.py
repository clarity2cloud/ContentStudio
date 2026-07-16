# app/services/image_service.py
import httpx
from PIL import Image
import io
from app.utils.logger import logger


class ImageService:
    def __init__(self):
        logger.info("✅ Image service initialized")

    async def download_image(self, url: str) -> bytes:
        """Download image from URL"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    async def convert_format(
            self,
            image_data: bytes,
            target_format: str = "png") -> bytes:
        """
        Convert image to target format (png or jpg)
        """
        try:
            img = Image.open(io.BytesIO(image_data))

            # Convert RGBA to RGB for JPEG
            if target_format.lower() in ("jpg", "jpeg"):
                if img.mode in ('RGBA', 'LA', 'P'):
                    # Create white background
                    rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                    if img.mode == 'P':
                        img = img.convert('RGBA')
                    rgb_img.paste(img, mask=img.split()
                                  [-1] if img.mode == 'RGBA' else None)
                    img = rgb_img
                else:
                    img = img.convert('RGB')

            output = io.BytesIO()
            save_format = 'JPEG' if target_format.lower() in ('jpg', 'jpeg') else 'PNG'
            img.save(output, format=save_format, quality=95)
            return output.getvalue()

        except Exception as e:
            logger.error(f"Image conversion failed: {str(e)}")
            # Return original if conversion fails
            return image_data

    async def optimize_for_web(
            self,
            image_data: bytes,
            max_size: int = 1024) -> bytes:
        """Resize image for web if too large"""
        try:
            img = Image.open(io.BytesIO(image_data))

            if max(img.size) > max_size:
                ratio = max_size / max(img.size)
                new_size = tuple(int(dim * ratio) for dim in img.size)
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            output = io.BytesIO()
            img.save(output, format='PNG' if img.mode == 'RGBA' else 'JPEG',
                     optimize=True, quality=85)
            return output.getvalue()

        except Exception as e:
            logger.error(f"Image optimization failed: {str(e)}")
            return image_data


# Singleton
image_service = ImageService()

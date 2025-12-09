"""
Gemini client using official Google Gen AI SDK.
Takes GEMINI_API_KEY from .env.
"""
import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

logger = logging.getLogger(__name__)

class GeminiClient:
    """Client for Google Gemini API using google-genai SDK."""

    def __init__(self, model: str = "gemini-3-pro-preview"):
        self.api_key = os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        
        self.model = model
        self.client = genai.Client(api_key=self.api_key)

    async def generate_content(
        self, 
        prompt: str, 
        response_schema: Optional[Dict[str, Any]] = None,
        retries: int = 5,
        base_delay: float = 2.0
    ) -> Any:
        """
        Generate content using Gemini model with retries.
        
        Args:
            prompt: The input text prompt.
            response_schema: Optional JSON schema for structured output.
            retries: Number of retries for transient errors.
            base_delay: Base delay for exponential backoff.
            
        Returns:
            The parsed JSON response if schema is provided, or the text content.
        """
        config_kwargs = {
            "temperature": 0.1, # Low temperature for factual extraction
        }
        
        if response_schema:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = response_schema

        config = types.GenerateContentConfig(**config_kwargs)

        last_exception = None
        
        for attempt in range(retries + 1):
            try:
                if attempt > 0:
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.info(f"Retry {attempt}/{retries} after {delay:.2f}s...")
                    await asyncio.sleep(delay)

                logger.info(f"Calling Gemini API ({self.model}), attempt {attempt + 1}...")
                
                # Use the async version of generate_content
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=config
                )
                
                # Extract text
                text_result = response.text
                
                if response_schema:
                    try:
                        return json.loads(text_result)
                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse JSON response: {text_result[:200]}...")
                        # If JSON is invalid, it might be a model error, treat as non-retriable unless we decide otherwise
                        # For now, let's raise
                        raise ValueError(f"Invalid JSON from Gemini: {e}")
                
                return text_result

            except Exception as e:
                # Determine if we should retry
                # Since we don't have exact exception types for the new SDK handy without installing,
                # we'll look at the error message or common patterns.
                error_str = str(e).lower()
                is_transient = any(x in error_str for x in ["503", "429", "timeout", "deadline", "unavailable"])
                
                if is_transient:
                    logger.warning(f"Transient error: {e}")
                    last_exception = e
                    continue
                else:
                    logger.error(f"Non-retriable error: {e}")
                    raise e
        
        if last_exception:
            raise last_exception

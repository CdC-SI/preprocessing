from typing import Dict, Any

from kserve import Model, ModelServer

from preprocessing.pipeline import run_pipeline

import logging
logger = logging.getLogger("pdf-ocr-predictor")

class PdfOCRModel(Model):
    def __init__(self, name: str):
        super().__init__(name)
        self.ready = True

    def load(self):
        """
        Called once when the pod starts.
        Load models, tokenizers, configs.
        """
        self.ready = True
        logger.info("Model '%s' loaded and ready", self.name)

    async def predict(self, payload: Dict[str, Any], headers=None):
        """
        Called on every HTTP request.
        """

        if "instances" in payload:
            # KServe v1 style
            b64_pdf = payload["instances"][0]["data_url"]
            user_uuid = payload["instances"][0]["user_uuid"]
            doc_title = payload["instances"][0]["doc_title"]
        else:
            logger.error("No 'instances' key in payload.")
            return {
                "documents": []
            }

        documents = await run_pipeline(b64_pdf, user_uuid, doc_title)

        return {
            "documents": documents
        }


if __name__ == "__main__":
    ModelServer().start(
        models=[PdfOCRModel("user-pdf-preprocessing")]
    )
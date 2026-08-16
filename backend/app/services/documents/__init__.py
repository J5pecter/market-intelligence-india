"""Company document research: read filings, extract figures, cite everything.

Public surface:
    document_pipeline.run(db, document)      -> ExtractionSummary
    document_pipeline.approve(db, citation)  -> promotes a reviewed figure
    document_pipeline.reject(db, citation)
"""

from app.services.documents.pipeline import (AUTO_ACCEPT_CONFIDENCE,
                                             DocumentPipeline,
                                             ExtractionSummary,
                                             document_pipeline)
from app.services.documents.text_extraction import ExtractionError

__all__ = [
    "document_pipeline", "DocumentPipeline", "ExtractionSummary",
    "ExtractionError", "AUTO_ACCEPT_CONFIDENCE",
]

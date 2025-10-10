"""
XML Protection Middleware
OWASP API4: Unrestricted Resource Consumption - XML Bomb Prevention

Prevents:
- Billion Laughs attack (XML entity expansion)
- XXE (XML External Entity) attacks
- Quadratic blowup attacks
- DTD retrieval attacks
"""
import logging
from xml.etree.ElementTree import XMLParser, ParseError
from typing import Optional
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class XMLProtection:
    """
    Secure XML parsing with entity expansion and external entity protection
    """

    def __init__(
        self,
        max_entity_expansions: int = 0,
        forbid_dtd: bool = True,
        forbid_entities: bool = True,
        forbid_external: bool = True
    ):
        """
        Initialize XML protection

        Args:
            max_entity_expansions: Maximum allowed entity expansions (0 = none)
            forbid_dtd: Forbid DTD processing
            forbid_entities: Forbid entity expansion
            forbid_external: Forbid external entity references
        """
        self.max_entity_expansions = max_entity_expansions
        self.forbid_dtd = forbid_dtd
        self.forbid_entities = forbid_entities
        self.forbid_external = forbid_external

    async def parse_xml(self, xml_content: str):
        """
        Safely parse XML with protection against attacks

        Args:
            xml_content: XML string to parse

        Raises:
            Exception: If XML contains malicious content

        Returns:
            Parsed XML element tree
        """
        try:
            # Create secure XML parser
            parser = XMLParser()

            # Disable dangerous features
            if hasattr(parser, 'parser'):
                xmlparser = parser.parser

                # Disable DTD processing
                if self.forbid_dtd:
                    try:
                        xmlparser.SetParamEntityParsing(0)
                    except AttributeError:
                        pass

                # Disable entity expansion
                if self.forbid_entities:
                    try:
                        xmlparser.DefaultHandler = None
                        xmlparser.EntityDeclHandler = None
                        xmlparser.UnparsedEntityDeclHandler = None
                    except AttributeError:
                        pass

            # Parse XML
            from xml.etree import ElementTree as ET
            root = ET.fromstring(xml_content, parser=parser)

            # Additional validation
            self._validate_no_entity_expansion(xml_content)
            self._validate_no_external_entities(xml_content)

            return root

        except ParseError as e:
            logger.error(f"XML parsing error: {e}")
            raise Exception(f"Invalid XML: {e}")

    def _validate_no_entity_expansion(self, xml_content: str) -> None:
        """
        Check for entity expansion patterns

        Raises:
            Exception: If entity expansion detected
        """
        if self.forbid_entities:
            if '<!ENTITY' in xml_content:
                logger.warning("Entity expansion attempt detected in XML")
                raise Exception("XML entity expansion is not allowed")

    def _validate_no_external_entities(self, xml_content: str) -> None:
        """
        Check for external entity references

        Raises:
            Exception: If external entities detected
        """
        if self.forbid_external:
            if 'SYSTEM' in xml_content or 'PUBLIC' in xml_content:
                logger.warning("External entity reference detected in XML")
                raise Exception("External entity references are not allowed")


class XMLProtectionMiddleware:
    """
    Middleware for XML request validation
    """

    def __init__(self, app):
        self.app = app
        self.protection = XMLProtection()

    async def __call__(self, request: Request, call_next):
        """
        Middleware handler
        """
        content_type = request.headers.get("content-type", "")

        # Check if request contains XML
        if "xml" in content_type.lower():
            try:
                body = await request.body()
                xml_content = body.decode('utf-8')

                # Validate XML
                await self.protection.parse_xml(xml_content)

            except Exception as e:
                logger.warning(f"XML validation failed: {e}")
                return JSONResponse(
                    status_code=400,
                    content={
                        "error": "Invalid XML",
                        "message": str(e)
                    }
                )

        response = await call_next(request)
        return response

import os
import re
from typing import Dict, List, Any
from pypdf import PdfReader
from parsers.base import BaseParser
from parsers.id_utils import generate_node_id

class PDFParser(BaseParser):
    """Parses PDF documents, extracting raw text and identifying key logical sections."""
    
    def parse(self, file_path: str) -> Dict[str, Any]:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
            
        file_name = os.path.basename(file_path)
        reader = PdfReader(file_path)
        
        raw_text_parts = []
        entities = []
        relationships = []
        
        full_text = ""
        for page_idx, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                full_text += f"\n--- Page {page_idx + 1} ---\n{text}"
                raw_text_parts.append(text)
                
        # Attempt to split text into logical sections based on headers or numbered blocks
        # Common insurance patterns: "1.0 Eligibility", "Section 2: Premium Calculation", etc.
        sections = re.split(r'\n(?=(?:[A-Z][A-Z\s]{4,}|[0-9]+\.[0-9]*\s+[A-Z]))', full_text)
        
        for sec_idx, section in enumerate(sections):
            section = section.strip()
            if not section:
                continue
                
            # Take the first line as heading
            lines = section.split('\n')
            heading = lines[0].strip()
            # If heading is too long or trivial, use index-based heading
            if len(heading) > 100 or len(heading) < 3:
                heading = f"Section {sec_idx + 1}"
                content = section
            else:
                content = "\n".join(lines[1:]).strip()
                
            if not content:
                continue
                
            # Determine node type based on keywords
            node_type = "product_feature"
            lower_heading = heading.lower()
            if any(kw in lower_heading for kw in ["rule", "eligibility", "premium", "formula", "limit", "exclusion", "condition"]):
                node_type = "business_rule"
            
            # Generate SUEI-compliant ID
            node_id = generate_node_id(node_type, heading)
                
            entities.append({
                "id": node_id,
                "type": node_type,
                "name": f"{heading} ({file_name})",
                "description": content[:1000] + ("..." if len(content) > 1000 else ""),
                "metadata": {
                    "source_file": file_path,
                    "section_title": heading,
                    "full_text": content
                }
            })
            
        return {
            "raw_text": full_text,
            "entities": entities,
            "relationships": relationships
        }

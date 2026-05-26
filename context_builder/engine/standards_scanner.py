import re
import os
from typing import Dict, Optional

class StandardsScanner:
    """Scans repository source files to dynamically extract naming conventions and indentation profiles."""
    
    def __init__(self, workspace_path: str):
        self.workspace_path = workspace_path
        
    def detect_standards(self) -> Optional[Dict[str, str]]:
        """Scans ingested source files to determine naming convention and indentation."""
        supported_exts = {".java", ".ts", ".js", ".py", ".go"}
        code_files = []
        
        # 1. Gather code files
        for root, dirs, files in os.walk(self.workspace_path):
            # Skip build or version control dirs
            dirs[:] = [d for d in dirs if d not in [".git", "node_modules", "target", "build", "dist", ".context_builder"]]
            for f in files:
                ext = os.path.splitext(f.lower())[1]
                if ext in supported_exts:
                    code_files.append(os.path.join(root, f))
                    
        # If no code files are present, return None to trigger standard template fallback
        if not code_files:
            return None
            
        indent_votes = {"2_spaces": 0, "4_spaces": 0, "tabs": 0}
        naming_votes = {"camelCase": 0, "snake_case": 0, "PascalCase": 0}
        
        # Compile helper regexes
        snake_re = re.compile(r'\b[a-z]+(?:_[a-z0-9]+)+\b')
        camel_re = re.compile(r'\b[a-z]+(?:[A-Z][a-zA-Z0-9]+)+\b')
        pascal_re = re.compile(r'\b[A-Z][a-zA-Z0-9]+\b')
        
        lines_scanned = 0
        files_scanned = 0
        for file_path in code_files:
            # Limit the scan to a maximum of 15 files to keep ingestion fast
            if files_scanned >= 15:
                break
            try:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    files_scanned += 1
                    for line in f:
                        lines_scanned += 1
                        if lines_scanned > 1500:
                            break
                            
                        # Indentation check
                        stripped = line.lstrip()
                        if not stripped:
                            continue
                        whitespace = line[:len(line) - len(stripped)]
                        if '\t' in whitespace:
                            indent_votes["tabs"] += 1
                        elif whitespace.startswith(" "):
                            space_count = len(whitespace)
                            if space_count % 4 == 0:
                                indent_votes["4_spaces"] += 1
                            elif space_count % 2 == 0:
                                indent_votes["2_spaces"] += 1
                                
                        # Naming convention tokens check
                        tokens = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', stripped)
                        for token in tokens:
                            if snake_re.match(token):
                                naming_votes["snake_case"] += 1
                            elif camel_re.match(token):
                                naming_votes["camelCase"] += 1
                            elif pascal_re.match(token):
                                # Avoid counting common short upper terms or SQL keywords as PascalCase
                                if len(token) > 3:
                                    naming_votes["PascalCase"] += 1
            except Exception:
                continue
                
        # Determine dominant standards
        total_indent_votes = sum(indent_votes.values())
        total_naming_votes = sum(naming_votes.values())
        
        detected_indent = max(indent_votes, key=indent_votes.get) if total_indent_votes > 0 else "2_spaces"
        detected_naming = max(naming_votes, key=naming_votes.get) if total_naming_votes > 0 else "camelCase"
        
        return {
            "naming_convention": detected_naming,
            "indentation": detected_indent
        }

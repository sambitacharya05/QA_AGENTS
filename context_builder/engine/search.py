import string
from rank_bm25 import BM25Okapi
from typing import List, Dict, Any

def tokenize(text: str) -> List[str]:
    """Simple tokenizer that lowers text and splits by whitespace and removes punctuation."""
    if not text:
        return []
    text = text.lower()
    text = text.translate(str.maketrans('', '', string.punctuation))
    return text.split()

class SemanticSearch:
    def __init__(self, nodes: List[Dict[str, Any]]):
        self.nodes = nodes
        self.tokenized_corpus = []
        for node in self.nodes:
            # Combine name and description for search corpus
            text = f"{node.get('name', '')} {node.get('description', '')}"
            self.tokenized_corpus.append(tokenize(text))
        
        if self.tokenized_corpus:
            self.bm25 = BM25Okapi(self.tokenized_corpus)
        else:
            self.bm25 = None

    def search(self, query: str, top_n: int = 10) -> List[Dict[str, Any]]:
        """Returns top matching nodes using BM25."""
        if not self.bm25 or not query:
            return []
            
        tokenized_query = tokenize(query)
        # get scores
        scores = self.bm25.get_scores(tokenized_query)
        
        # rank nodes
        scored_nodes = [(score, node) for score, node in zip(scores, self.nodes) if score > 0]
        scored_nodes.sort(key=lambda x: x[0], reverse=True)
        
        return [node for score, node in scored_nodes[:top_n]]

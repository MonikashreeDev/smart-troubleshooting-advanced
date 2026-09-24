"""Appendix B transport envelope. Kept separate from the Appendix A core contract."""
from dataclasses import dataclass
from typing import Any, Dict, List
@dataclass
class APIEnvelope:
    query:str; query_variations:List[str]; response:Dict[str,Any]; meta:Dict[str,Any]
    def to_dict(self): return self.__dict__

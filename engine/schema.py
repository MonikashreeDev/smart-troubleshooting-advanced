"""Appendix A contract, represented with dependency-free dataclasses."""
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

class Condition(str,Enum): greater='greater'; equal='equal'; less='less'
class ResultTypes(str,Enum): boolean='boolean'; intNum='integer'; string='str'; floatNum='float'
class actionCategory(str,Enum): auto='auto'; manual='manual'; critical='critical'
@dataclass(frozen=True)
class BaseDeeplink: deeplink:str
@dataclass(frozen=True)
class Deeplink(BaseDeeplink):
    description:str; message:str=''; classes:Optional[Dict[str,str]]=None; originalType:Optional[str]=None
@dataclass(frozen=True)
class ValidationDeeplink(BaseDeeplink):
    key:str=''; resultType:Optional[str]=None; condition:Optional[str]=None; value:Optional[str]=None
@dataclass(frozen=True)
class StepGroup:
    steps:List[str]; validationDeeplink:Optional[ValidationDeeplink]=None; actionableDeeplink:Optional[Deeplink]=None
@dataclass(frozen=True)
class Action:
    actionName:str; description:str; stepGroups:List[StepGroup]; category:str=actionCategory.manual.value
@dataclass(frozen=True)
class Goal:
    goal:str; title:str; actions:List[Action]; score:float
@dataclass(frozen=True)
class ContextDeeplinkResponse:
    contexts:List[Goal]=field(default_factory=list)
    def to_dict(self):
        def clean(x):
            if hasattr(x,'__dataclass_fields__'): return {k:clean(v) for k,v in asdict(x).items()}
            if isinstance(x,list): return [clean(v) for v in x]
            return x
        return clean(self)

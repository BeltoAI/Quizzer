from __future__ import annotations
from typing import List, Literal, Union
from pydantic import BaseModel, Field

class MCQ(BaseModel):
    type: Literal["mcq"] = "mcq"
    prompt: str
    choices: List[str]
    answer: int = 0
    points: int = 1

class TrueFalse(BaseModel):
    type: Literal["truefalse"] = "truefalse"
    prompt: str
    answer: bool
    points: int = 1

class Short(BaseModel):
    type: Literal["short"] = "short"
    prompt: str
    points: int = 1

class FillBlank(BaseModel):
    type: Literal["fillblank"] = "fillblank"
    prompt: str
    answer: str
    points: int = 1

Question = Union[MCQ, TrueFalse, Short, FillBlank]

class Quiz(BaseModel):
    title: str
    questions: List[Question] = Field(default_factory=list)

class Midterm(BaseModel):
    title: str
    questions: List[Question] = Field(default_factory=list)

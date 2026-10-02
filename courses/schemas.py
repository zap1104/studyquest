"""
courses/schemas.py

The single source of truth for StudyQuest curriculum generation.
Both mock fallback generators and Gemini output validate against this schema.
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, model_validator

# --------------------------------------------------
# 1. INPUT VALIDATION CONTRACT
# --------------------------------------------------
StudyGoal = Literal["quick_cram", "balanced_review", "deep_learning"]
AssessmentFormat = Literal[
    "multiple_choice", "identification", "enumeration"
]

ASSESSMENT_MIXES = {
    frozenset({"multiple_choice"}): {
        "multiple_choice": 7, "true_false": 3, "identification": 0, "enumeration": 0,
    },
    frozenset({"identification"}): {
        "multiple_choice": 3, "true_false": 2, "identification": 5, "enumeration": 0,
    },
    frozenset({"enumeration"}): {
        "multiple_choice": 3, "true_false": 2, "identification": 0, "enumeration": 5,
    },
    frozenset({"multiple_choice", "identification"}): {
        "multiple_choice": 4, "true_false": 2, "identification": 4, "enumeration": 0,
    },
    frozenset({"multiple_choice", "enumeration"}): {
        "multiple_choice": 4, "true_false": 2, "identification": 0, "enumeration": 4,
    },
    frozenset({"identification", "enumeration"}): {
        "multiple_choice": 2, "true_false": 2, "identification": 3, "enumeration": 3,
    },
    frozenset({"multiple_choice", "identification", "enumeration"}): {
        "multiple_choice": 3, "true_false": 2, "identification": 3, "enumeration": 2,
    },
}


def get_assessment_mix(assessment_formats):
    formats = frozenset(assessment_formats or ["multiple_choice"])
    try:
        return dict(ASSESSMENT_MIXES[formats])
    except KeyError as error:
        raise ValueError("Assessment formats must include one or more supported formats.") from error

class GenerationPreferences(BaseModel):
    study_goal: StudyGoal = "quick_cram"
    assessment_formats: List[AssessmentFormat] = Field(
        default_factory=lambda: ["multiple_choice"]
    )

    @model_validator(mode="after")
    def validate_assessment_formats(self):
        if not self.assessment_formats:
            raise ValueError("Select at least one assessment format.")
        self.assessment_formats = list(dict.fromkeys(self.assessment_formats))
        return self

class GenerationProfile(BaseModel):
    study_goal: StudyGoal
    assessment_formats: List[AssessmentFormat]

# --------------------------------------------------
# 2. QUIZ CONTRACT
# --------------------------------------------------
QuestionType = Literal[
    "multiple_choice", "true_false", "identification", "enumeration"
]

class GeneratedChoice(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    is_correct: bool

class GeneratedQuestion(BaseModel):
    order: int
    type: QuestionType = "multiple_choice"
    difficulty: Optional[str] = "medium"
    text: str = Field(min_length=5, max_length=500)
    explanation: str = Field(min_length=10, max_length=800)
    choices: List[GeneratedChoice] = Field(default_factory=list)
    accepted_answers: List[str] = Field(default_factory=list)
    expected_items: List[dict] = Field(default_factory=list)
    order_matters: bool = False
    topic_keys: Optional[List[str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_question_integrity(self):
        # 1. Choice Count Check
        if self.type == "multiple_choice":
            if not (2 <= len(self.choices) <= 4):
                raise ValueError(
                    f"Question {self.order} (multiple_choice) requires between 2 and 4 choices; got {len(self.choices)}."
                )
        elif self.type == "true_false":
            if len(self.choices) != 2:
                raise ValueError(
                    f"Question {self.order} (true_false) requires exactly 2 choices; got {len(self.choices)}."
                )
            choice_texts = {c.text.strip().lower() for c in self.choices}
            if choice_texts != {"true", "false"}:
                raise ValueError(
                    f"Question {self.order} is true_false but choices are not ['True', 'False']."
                )

        if self.type in {"multiple_choice", "true_false"}:
            correct_count = sum(1 for c in self.choices if c.is_correct)
            if correct_count != 1:
                raise ValueError(
                    f"Question {self.order} must have exactly 1 correct choice; got {correct_count}."
                )

            texts_lower = [c.text.strip().lower() for c in self.choices]
            if len(texts_lower) != len(set(texts_lower)):
                raise ValueError(f"Question {self.order} has duplicate choice text.")
        elif self.type == "identification":
            if not self.accepted_answers:
                raise ValueError(
                    f"Question {self.order} (identification) requires an accepted answer."
                )
            if self.choices or self.expected_items:
                raise ValueError(
                    f"Question {self.order} (identification) cannot include choices or expected items."
                )
        elif self.type == "enumeration":
            if len(self.expected_items) < 2:
                raise ValueError(
                    f"Question {self.order} (enumeration) requires at least 2 expected items."
                )
            if self.choices or self.accepted_answers:
                raise ValueError(
                    f"Question {self.order} (enumeration) cannot include choices or accepted answers."
                )
            for item in self.expected_items:
                if not item.get("canonical"):
                    raise ValueError(
                        f"Question {self.order} has an enumeration item without a canonical answer."
                    )
                item.setdefault("accepted_variants", [])

        return self

class GeneratedQuiz(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    questions: List[GeneratedQuestion] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_no_duplicate_questions(self):
        texts_lower = [q.text.strip().lower() for q in self.questions]
        if len(texts_lower) != len(set(texts_lower)):
            raise ValueError(f"Quiz '{self.title}' has duplicate question text.")
        return self

# --------------------------------------------------
# 3. LESSON CONTENT CONTRACT
# --------------------------------------------------
class GeneratedAnalogy(BaseModel):
    label: Optional[str] = None
    explanation: str
    source_reference: Optional[str] = None

class GeneratedConcept(BaseModel):
    term: str = Field(min_length=1, max_length=150)
    formal_definition: str = Field(min_length=5)
    simple_explanation: str = Field(min_length=5)
    analogy: Optional[str] = None
    examples: Optional[List[str]] = Field(default_factory=list)

class GeneratedSection(BaseModel):
    order: int
    title: str = Field(min_length=1, max_length=200)
    introduction: str = Field(default="")
    concepts: List[GeneratedConcept] = Field(default_factory=list)
    key_points: List[str] = Field(default_factory=list)

class GeneratedKeyTerm(BaseModel):
    term: str = Field(min_length=1, max_length=150)
    definition: str = Field(min_length=5)
    source_reference: Optional[str] = None

class ComparisonRow(BaseModel):
    criterion: str
    values: List[str]

class GeneratedComparison(BaseModel):
    title: str
    columns: List[str]
    rows: List[ComparisonRow]

class GeneratedEnumeration(BaseModel):
    title: str
    prompt: str
    items: List[str]
    order_matters: bool = False
    memory_cue: Optional[str] = None

class GeneratedCommonConfusion(BaseModel):
    concept_a: str
    concept_b: str
    difference: str

class GeneratedTopic(BaseModel):
    key: Optional[str] = Field(default="", max_length=140)
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default="", max_length=500)

    @model_validator(mode="after")
    def normalize_key(self):
        from django.utils.text import slugify
        raw_key = (self.key or "").strip()
        cleaned_key = slugify(raw_key)
        if not cleaned_key:
            cleaned_key = slugify((self.name or "").strip())
        if not cleaned_key:
            raise ValueError(f"Topic '{self.name}' produced an empty key after normalization.")
        self.key = cleaned_key
        self.name = self.name.strip()
        self.description = (self.description or "").strip()
        return self

class GeneratedChapter(BaseModel):
    order: int
    title: str = Field(min_length=1, max_length=200)
    week_label: Optional[str] = None
    focus: Optional[str] = Field(default="")
    overview: str = Field(min_length=5)
    analogy: Optional[GeneratedAnalogy] = None  # Expected by test_hand_transcribed_chapter_validates
    estimated_minutes: int = Field(default=15, ge=2, le=60)
    learning_objectives: List[str] = Field(default_factory=list)
    topics: Optional[List[GeneratedTopic]] = Field(default_factory=list)
    sections: List[GeneratedSection] = Field(default_factory=list)
    key_terms: Optional[List[GeneratedKeyTerm]] = Field(default_factory=list)
    comparisons: Optional[List[GeneratedComparison]] = Field(default_factory=list)
    enumerations: Optional[List[GeneratedEnumeration]] = Field(default_factory=list)
    common_confusions: Optional[List[GeneratedCommonConfusion]] = Field(default_factory=list)
    key_takeaways: Optional[List[str]] = Field(default_factory=list)
    source_files: Optional[List[str]] = Field(default_factory=list, max_length=3)
    primary_source_file: Optional[str] = None
    quiz: GeneratedQuiz

    @model_validator(mode="after")
    def validate_and_deduplicate_topics(self):
        """Deduplicates topics within this chapter by normalized key."""
        if not self.topics:
            self.topics = []
            return self

        seen_keys = set()
        deduped = []
        for topic in self.topics:
            if topic.key not in seen_keys:
                seen_keys.add(topic.key)
                deduped.append(topic)
            else:
                import logging
                logging.getLogger(__name__).warning(
                    f"Chapter '{self.title}' declared duplicate topic key '{topic.key}'. Keeping first declaration."
                )
        self.topics = deduped
        return self

    @model_validator(mode="after")
    def validate_chapter_question_topic_keys(self):
        """Ensures question topic_keys reference only topics declared in this chapter.

        Undeclared or cross-chapter keys are pruned with an internal warning,
        preserving question validity.
        """
        chapter_keys = {topic.key for topic in (self.topics or [])}
        for question in getattr(self.quiz, "questions", []):
            if not getattr(question, "topic_keys", None):
                question.topic_keys = []
                continue

            from django.utils.text import slugify
            valid_keys = []
            for raw_k in question.topic_keys:
                norm_k = slugify(str(raw_k).strip())
                if norm_k in chapter_keys:
                    if norm_k not in valid_keys:
                        valid_keys.append(norm_k)
                else:
                    import logging
                    logging.getLogger(__name__).warning(
                        f"Question {question.order} in chapter '{self.title}' "
                        f"referenced undeclared topic key: '{raw_k}'"
                    )
            question.topic_keys = valid_keys
        return self

# --------------------------------------------------
# 4. ROOT JOURNEY CONTRACT
# --------------------------------------------------
class GeneratedCourseMeta(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=500)
    source_type: Optional[str] = "reviewer"
    difficulty: Optional[str] = "intermediate"
    source_bundle_count: Optional[int] = Field(default=1, ge=1, le=3)
    source_filenames: Optional[List[str]] = Field(default_factory=list)

class GeneratedJourney(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    generation_profile: Optional[GenerationProfile] = None
    course: GeneratedCourseMeta
    chapters: List[GeneratedChapter] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sequential_chapter_order(self):
        orders = [c.order for c in self.chapters]
        expected = list(range(1, len(self.chapters) + 1))
        if orders != expected:
            raise ValueError(
                f"Chapter order values must be sequential starting at 1; got {orders}."
            )
        return self
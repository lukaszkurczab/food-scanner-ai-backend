"""Lightweight text classifier for AI gateway moderation decisions."""

from __future__ import annotations
# pyright: reportMissingImports=false

from pathlib import Path
from typing import Any

try:
    import joblib
except ModuleNotFoundError:  # pragma: no cover - depends on optional dependency
    joblib = None

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
except ModuleNotFoundError:  # pragma: no cover - depends on optional dependency
    TfidfVectorizer = None
    LogisticRegression = None


class AiClassifier:
    """Binary classifier that predicts the probability of a prompt being on-topic."""

    def __init__(self) -> None:
        self.vectorizer: Any | None = None
        self.classifier: Any | None = None

    def _require_dependencies(self) -> None:
        if TfidfVectorizer is None or LogisticRegression is None or joblib is None:
            raise RuntimeError(
                "AiClassifier requires scikit-learn and joblib to be installed."
            )

    def _get_vectorizer_class(self) -> type[Any]:
        self._require_dependencies()
        assert TfidfVectorizer is not None
        return TfidfVectorizer

    def _get_classifier_class(self) -> type[Any]:
        self._require_dependencies()
        assert LogisticRegression is not None
        return LogisticRegression

    def _get_joblib_module(self) -> Any:
        self._require_dependencies()
        assert joblib is not None
        return joblib

    def train(self, texts: list[str], labels: list[int]) -> None:
        """Vectorize the provided texts and train a logistic regression classifier."""
        if len(texts) != len(labels):
            raise ValueError("Texts and labels must have the same length.")
        if not texts:
            raise ValueError("Training data cannot be empty.")
        if len(set(labels)) < 2:
            raise ValueError("Training labels must contain at least two classes.")

        vectorizer_class = self._get_vectorizer_class()
        classifier_class = self._get_classifier_class()

        vectorizer = vectorizer_class(
            lowercase=True,
            ngram_range=(1, 2),
            min_df=1,
        )
        training_matrix = vectorizer.fit_transform(texts)
        classifier = classifier_class(max_iter=1000, random_state=42)
        classifier.fit(training_matrix, labels)
        self.vectorizer = vectorizer
        self.classifier = classifier

    def predict(self, text: str) -> float:
        """Return the probability of the text being on-topic."""
        if self.vectorizer is None or self.classifier is None:
            raise RuntimeError("AiClassifier must be trained or loaded before prediction.")

        feature_matrix = self.vectorizer.transform([text])
        probabilities = self.classifier.predict_proba(feature_matrix)[0]
        classes = list(self.classifier.classes_)
        on_topic_index = classes.index(1)
        return float(probabilities[on_topic_index])

    def save_model(self, path: str | Path) -> None:
        """Persist the vectorizer and classifier to disk."""
        if self.vectorizer is None or self.classifier is None:
            raise RuntimeError("AiClassifier must be trained before saving.")

        joblib_module = self._get_joblib_module()
        target_path = Path(path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        joblib_module.dump(
            {
                "vectorizer": self.vectorizer,
                "classifier": self.classifier,
            },
            target_path,
        )

    def load_model(self, path: str | Path) -> None:
        """Load a persisted vectorizer and classifier from disk."""
        joblib_module = self._get_joblib_module()

        payload = joblib_module.load(Path(path))
        self.vectorizer = payload["vectorizer"]
        self.classifier = payload["classifier"]

import json
import os
from importlib import resources

from openai import OpenAI

from quizz.engine.llm import GenerateRequest
from quizz.engine.models import Quiz


def _no_token_error() -> str:
    raise RuntimeError(
        "GitHub Models requires GITHUB_TOKEN to be set. "
        "In Actions: add `permissions: models: read`. Locally: export a PAT with the models scope."
    )


def _load_prompt(name: str) -> str:
    return resources.files("quizz.engine.prompts").joinpath(name).read_text()


class GitHubModelsLLM:
    def __init__(
        self,
        base_url: str = "https://models.github.ai/inference",
        token: str | None = None,
        model: str = "gpt-4o-mini",
    ) -> None:
        self._model = model
        self._client = OpenAI(
            base_url=base_url,
            api_key=token or os.environ.get("GITHUB_TOKEN") or _no_token_error(),
        )

    def generate_quiz(self, req: GenerateRequest) -> Quiz:
        files_blob = "\n\n".join(
            f"--- {path} ---\n{content}" for path, content in req.files.items()
        )
        prompt = _load_prompt("generate.txt").format(
            schema=Quiz.model_json_schema(),
            pr_title=req.pr_title,
            pr_body=req.pr_body,
            diff=req.diff,
            files=files_blob,
        )
        # Try strict structured output first (OpenAI's `parse` API enforces the Pydantic schema).
        # GitHub Models may not support all schema features — fall back to free-form JSON on failure.
        try:
            parsed = self._client.beta.chat.completions.parse(
                model=req.model,
                response_format=Quiz,
                messages=[{"role": "user", "content": prompt}],
            )
            result = parsed.choices[0].message.parsed
            if result is None:
                raise ValueError("structured-output parsing returned None")
            return result
        except Exception as e:
            # Fall back to free-form JSON + manual validation. Print raw content for diagnosis.
            print(
                f"[quizz] strict parse failed ({type(e).__name__}: {e}); falling back to json_object mode",
                flush=True,
            )
            resp = self._client.chat.completions.create(
                model=req.model,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
            content = resp.choices[0].message.content or "{}"
            try:
                return Quiz.model_validate_json(content)
            except Exception:
                print(f"[quizz] LLM raw response:\n{content}", flush=True)
                raise

    def grade_open(self, question_prompt: str, rubric: str, answer: str) -> tuple[int, str]:
        prompt = _load_prompt("grade_open.txt").format(
            prompt=question_prompt,
            rubric=rubric,
            answer=answer,
        )
        resp = self._client.chat.completions.create(
            model=self._model,  # USE the stored model
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        score = int(data.get("score", 0))
        score = max(0, min(100, score))  # CLAMP — protects against bad LLM output
        return score, str(data.get("feedback", ""))

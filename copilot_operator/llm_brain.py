"""LLM Brain — external AI API integration for intelligent reasoning.

Supports multiple providers:
  - OpenAI (GPT-4o, GPT-4, GPT-3.5)
  - Anthropic (Claude 3.5, Claude 3)
  - Google Gemini (gemini-pro, gemini-1.5-pro)
  - Local / self-hosted (Ollama, LM Studio, any OpenAI-compatible endpoint)

The LLM brain gives the operator *real* intelligence: it can analyse code,
diagnose stuck loops with context, suggest strategies, review diffs, and
produce structured JSON decisions.

Usage:
    brain = LLMBrain.from_config(config)
    response = brain.ask("Analyse this failure pattern", system="You are a code analyst")
    structured = brain.ask_json(prompt, schema_hint="Return {action, reason, confidence}")
"""

from __future__ import annotations

__all__ = [
    'SUPPORTED_PROVIDERS',
    'LLMConfig',
    'LLMResponse',
    'LLMBrain',
    'load_llm_config_from_env',
    'load_llm_config_from_dict',
    'estimate_cost_usd',
    'render_brain_status',
]

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUPPORTED_PROVIDERS = ('openai', 'anthropic', 'gemini', 'xai', 'local')

_DEFAULT_MODELS: dict[str, str] = {
    'openai': 'gpt-4o',
    'anthropic': 'claude-sonnet-4-20250514',
    'gemini': 'gemini-1.5-pro',
    'xai': 'grok-3-mini',
    'local': 'default',
}

_API_URLS: dict[str, str] = {
    'openai': 'https://api.openai.com/v1/chat/completions',
    'anthropic': 'https://api.anthropic.com/v1/messages',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
    'xai': 'https://api.x.ai/v1/chat/completions',
    'local': 'http://localhost:11434/v1/chat/completions',
}

_TOKEN_ENV_VARS: dict[str, str] = {
    'openai': 'OPENAI_API_KEY',
    'anthropic': 'ANTHROPIC_API_KEY',
    'gemini': 'GEMINI_API_KEY',
    'xai': 'XAI_API_KEY',
    'local': 'LOCAL_LLM_API_KEY',
}

# Token limits per provider for safety
_MAX_TOKENS_DEFAULT: dict[str, int] = {
    'openai': 4096,
    'anthropic': 4096,
    'gemini': 4096,
    'xai': 4096,
    'local': 2048,
}

# ---------------------------------------------------------------------------
# Cost tracking — USD per 1K tokens (input, output)
# Prices as of April 2026 — approximate, for estimation only
# ---------------------------------------------------------------------------
_COST_PER_1K: dict[str, tuple[float, float]] = {
    # (input_per_1k, output_per_1k)
    'gpt-4o': (0.005, 0.015),
    'gpt-4o-mini': (0.00015, 0.0006),
    'gpt-4-turbo': (0.01, 0.03),
    'gpt-3.5-turbo': (0.0005, 0.0015),
    'claude-sonnet-4-20250514': (0.003, 0.015),
    'claude-3-5-sonnet-20241022': (0.003, 0.015),
    'claude-3-haiku-20240307': (0.00025, 0.00125),
    'claude-3-opus-20240229': (0.015, 0.075),
    'gemini-1.5-pro': (0.00125, 0.005),
    'gemini-1.5-flash': (0.000075, 0.0003),
    'gemini-pro': (0.000125, 0.000375),
    # xAI Grok
    'grok-3-mini': (0.0003, 0.0005),
    'grok-3': (0.003, 0.015),
}


def estimate_cost_usd(model: str, tokens: int) -> float:
    """Estimate USD cost for a given model and token count.

    Assumes a 70/30 input/output split for simplicity when we only have
    total token count.  Returns 0.0 for local/unknown models.
    """
    prices = _COST_PER_1K.get(model)
    if not prices:
        return 0.0
    input_tokens = int(tokens * 0.7)
    output_tokens = tokens - input_tokens
    return (input_tokens / 1000 * prices[0]) + (output_tokens / 1000 * prices[1])


@dataclass(slots=True)
class LLMConfig:
    """Configuration for the LLM brain."""
    provider: str = ''           # openai, anthropic, gemini, local
    model: str = ''              # gpt-4o, claude-sonnet-4-20250514, etc.
    api_key: str = ''            # loaded from env or config
    api_url: str = ''            # Override for custom endpoints
    max_tokens: int = 4096
    temperature: float = 0.3    # Low temp for structured reasoning
    timeout_seconds: int = 60
    enabled: bool = False

    @property
    def is_ready(self) -> bool:
        """Check if the LLM brain is properly configured and ready to use."""
        if not self.enabled:
            return False
        if not self.provider or self.provider not in SUPPORTED_PROVIDERS:
            return False
        if self.provider != 'local' and not self.api_key:
            return False
        return True


@dataclass(slots=True)
class LLMResponse:
    """Structured response from the LLM."""
    content: str = ''
    model: str = ''
    provider: str = ''
    tokens_used: int = 0
    finish_reason: str = ''
    success: bool = False
    error: str = ''
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _build_openai_request(
    config: LLMConfig,
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, str], bytes]:
    """Build request for OpenAI-compatible APIs (also used for local)."""
    url = config.api_url or _API_URLS.get(config.provider, _API_URLS['openai'])
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {config.api_key}',
    }
    body = json.dumps({
        'model': config.model or _DEFAULT_MODELS.get(config.provider, 'gpt-4o'),
        'messages': messages,
        'max_tokens': config.max_tokens,
        'temperature': config.temperature,
    }).encode('utf-8')
    return url, headers, body


def _parse_openai_response(data: dict[str, Any], provider: str) -> LLMResponse:
    """Parse OpenAI-format response."""
    choices = data.get('choices', [])
    if not choices:
        return LLMResponse(success=False, error='No choices in response', provider=provider, raw=data)
    choice = choices[0]
    message = choice.get('message', {})
    usage = data.get('usage', {})
    return LLMResponse(
        content=message.get('content', ''),
        model=data.get('model', ''),
        provider=provider,
        tokens_used=usage.get('total_tokens', 0),
        finish_reason=choice.get('finish_reason', ''),
        success=True,
        raw=data,
    )


def _build_anthropic_request(
    config: LLMConfig,
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, str], bytes]:
    """Build request for Anthropic Claude API."""
    url = config.api_url or _API_URLS['anthropic']
    headers = {
        'Content-Type': 'application/json',
        'x-api-key': config.api_key,
        'anthropic-version': '2023-06-01',
    }
    # Anthropic uses system as a top-level field, not in messages
    system_text = ''
    user_messages = []
    for msg in messages:
        if msg['role'] == 'system':
            system_text = msg['content']
        else:
            user_messages.append(msg)

    payload: dict[str, Any] = {
        'model': config.model or _DEFAULT_MODELS['anthropic'],
        'max_tokens': config.max_tokens,
        'temperature': config.temperature,
        'messages': user_messages,
    }
    if system_text:
        payload['system'] = system_text

    body = json.dumps(payload).encode('utf-8')
    return url, headers, body


def _parse_anthropic_response(data: dict[str, Any]) -> LLMResponse:
    """Parse Anthropic Claude API response."""
    content_blocks = data.get('content', [])
    text = ''
    for block in content_blocks:
        if block.get('type') == 'text':
            text += block.get('text', '')

    usage = data.get('usage', {})
    return LLMResponse(
        content=text,
        model=data.get('model', ''),
        provider='anthropic',
        tokens_used=usage.get('input_tokens', 0) + usage.get('output_tokens', 0),
        finish_reason=data.get('stop_reason', ''),
        success=True,
        raw=data,
    )


def _build_gemini_request(
    config: LLMConfig,
    messages: list[dict[str, str]],
) -> tuple[str, dict[str, str], bytes]:
    """Build request for Google Gemini API."""
    model = config.model or _DEFAULT_MODELS['gemini']
    url = (config.api_url or _API_URLS['gemini']).format(model=model)
    url = f'{url}?key={config.api_key}'
    headers = {'Content-Type': 'application/json'}

    # Convert messages to Gemini format
    contents = []
    system_instruction = ''
    for msg in messages:
        if msg['role'] == 'system':
            system_instruction = msg['content']
        else:
            role = 'user' if msg['role'] == 'user' else 'model'
            contents.append({
                'role': role,
                'parts': [{'text': msg['content']}],
            })

    payload: dict[str, Any] = {
        'contents': contents,
        'generationConfig': {
            'maxOutputTokens': config.max_tokens,
            'temperature': config.temperature,
        },
    }
    if system_instruction:
        payload['systemInstruction'] = {'parts': [{'text': system_instruction}]}

    body = json.dumps(payload).encode('utf-8')
    return url, headers, body


def _parse_gemini_response(data: dict[str, Any]) -> LLMResponse:
    """Parse Google Gemini API response."""
    candidates = data.get('candidates', [])
    if not candidates:
        return LLMResponse(success=False, error='No candidates in Gemini response', provider='gemini', raw=data)
    content = candidates[0].get('content', {})
    parts = content.get('parts', [])
    text = ''.join(part.get('text', '') for part in parts)
    usage = data.get('usageMetadata', {})
    return LLMResponse(
        content=text,
        model='gemini',
        provider='gemini',
        tokens_used=usage.get('totalTokenCount', 0),
        finish_reason=candidates[0].get('finishReason', ''),
        success=True,
        raw=data,
    )


# ---------------------------------------------------------------------------
# Core HTTP call with circuit breaker
# ---------------------------------------------------------------------------

# Simple circuit breaker state
_circuit_breaker: dict[str, Any] = {
    'failures': 0,
    'last_failure': 0.0,
    'open': False,
}

_MAX_FAILURES = 3
_COOLDOWN_SECONDS = 60.0


def _check_circuit_breaker(provider: str) -> str | None:
    """Return an error message if the circuit is open, else None."""
    import time
    if _circuit_breaker['open']:
        elapsed = time.monotonic() - _circuit_breaker['last_failure']
        if elapsed < _COOLDOWN_SECONDS:
            return f'{provider} circuit breaker open — {_COOLDOWN_SECONDS - elapsed:.0f}s cooldown remaining'
        # Half-open: allow one retry
        _circuit_breaker['open'] = False
        _circuit_breaker['failures'] = _MAX_FAILURES - 1
    return None


def _record_failure() -> None:
    import time
    _circuit_breaker['failures'] += 1
    _circuit_breaker['last_failure'] = time.monotonic()
    if _circuit_breaker['failures'] >= _MAX_FAILURES:
        _circuit_breaker['open'] = True
        logger.warning('Circuit breaker OPEN after %d failures — cooling down %ss',
                       _circuit_breaker['failures'], _COOLDOWN_SECONDS)


def _record_success() -> None:
    _circuit_breaker['failures'] = 0
    _circuit_breaker['open'] = False


def _call_llm(config: LLMConfig, messages: list[dict[str, str]]) -> LLMResponse:
    """Make the actual HTTP call to the LLM provider with circuit breaker."""
    if not config.is_ready:
        return LLMResponse(success=False, error=f'LLM brain not ready: provider={config.provider}, has_key={bool(config.api_key)}')

    # Check circuit breaker
    cb_error = _check_circuit_breaker(config.provider)
    if cb_error:
        return LLMResponse(success=False, error=cb_error, provider=config.provider)

    try:
        if config.provider == 'anthropic':
            url, headers, body = _build_anthropic_request(config, messages)
        elif config.provider == 'gemini':
            url, headers, body = _build_gemini_request(config, messages)
        else:
            url, headers, body = _build_openai_request(config, messages)

        req = urllib.request.Request(url, data=body, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=config.timeout_seconds) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        if config.provider == 'anthropic':
            result = _parse_anthropic_response(data)
        elif config.provider == 'gemini':
            result = _parse_gemini_response(data)
        else:
            result = _parse_openai_response(data, config.provider)

        if result.success:
            _record_success()
        return result

    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode('utf-8', errors='replace')[:500] if exc.fp else ''
        error_msg = f'{config.provider} API returned {exc.code}: {error_body}'
        logger.error(error_msg)
        _record_failure()
        # Retry on rate limit (429) with backoff hint
        if exc.code == 429:
            error_msg += ' (rate limited — circuit breaker will throttle retries)'
        return LLMResponse(success=False, error=error_msg, provider=config.provider)
    except (urllib.error.URLError, OSError) as exc:
        error_msg = f'{config.provider} connection error: {exc}'
        logger.error(error_msg)
        _record_failure()
        return LLMResponse(success=False, error=error_msg, provider=config.provider)
    except Exception as exc:
        error_msg = f'{config.provider} unexpected error: {exc}'
        logger.error(error_msg)
        _record_failure()
        return LLMResponse(success=False, error=error_msg, provider=config.provider)


# ---------------------------------------------------------------------------
# LLM Brain — high-level interface
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r'```(?:json)?\s*(.*?)\s*```', re.DOTALL)


class LLMBrain:
    """High-level interface to the LLM brain.

    Usage:
        brain = LLMBrain(config)
        response = brain.ask("What should I do next?")
        data = brain.ask_json(prompt, schema_hint="{action, reason}")
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self._call_count = 0
        self._total_tokens = 0
        self._total_cost_usd = 0.0

    @classmethod
    def from_env(cls, provider: str = '', model: str = '', api_url: str = '') -> LLMBrain:
        """Create an LLMBrain from environment variables."""
        config = load_llm_config_from_env(provider, model, api_url)
        return cls(config)

    @property
    def is_ready(self) -> bool:
        return self.config.is_ready

    @property
    def stats(self) -> dict[str, Any]:
        return {
            'provider': self.config.provider,
            'model': self.config.model,
            'enabled': self.config.enabled,
            'ready': self.is_ready,
            'calls': self._call_count,
            'total_tokens': self._total_tokens,
            'estimated_cost_usd': round(self._total_cost_usd, 6),
        }

    def ask(
        self,
        prompt: str,
        system: str = '',
        context: str = '',
    ) -> LLMResponse:
        """Send a prompt to the LLM and get a response.

        Args:
            prompt: The main question or instruction.
            system: Optional system prompt for role/behaviour setting.
            context: Optional additional context prepended to the prompt.
        """
        messages: list[dict[str, str]] = []
        if system:
            messages.append({'role': 'system', 'content': system})
        full_prompt = f'{context}\n\n{prompt}' if context else prompt
        messages.append({'role': 'user', 'content': full_prompt})

        response = _call_llm(self.config, messages)
        self._call_count += 1
        if response.success:
            self._total_tokens += response.tokens_used
            self._total_cost_usd += estimate_cost_usd(self.config.model, response.tokens_used)
        return response

    def ask_json(
        self,
        prompt: str,
        system: str = '',
        context: str = '',
        schema_hint: str = '',
    ) -> tuple[dict[str, Any] | None, LLMResponse]:
        """Ask the LLM and parse the response as JSON.

        Returns (parsed_dict_or_None, raw_response).
        """
        json_instruction = '\nRespond with valid JSON only.'
        if schema_hint:
            json_instruction += f' Expected schema: {schema_hint}'

        response = self.ask(prompt + json_instruction, system=system, context=context)
        if not response.success:
            return None, response

        parsed = _extract_json(response.content)
        return parsed, response

    # ----- Specialised reasoning methods -----

    def analyse_failure(
        self,
        goal: str,
        history_summary: str,
        current_error: str,
    ) -> tuple[dict[str, Any] | None, LLMResponse]:
        """Analyse why the operator is stuck and suggest a strategy."""
        system = (
            'You are an expert software engineering AI analyst. '
            'Analyse the operator\'s failure pattern and provide a concrete strategy. '
            'Be specific about what to change. Do NOT be vague.'
        )
        prompt = f"""The Copilot Operator is stuck. Analyse and provide a recovery strategy.

Goal: {goal}

Recent history:
{history_summary}

Current error/blocker:
{current_error}

Respond with JSON:
{{
  "root_cause": "what went wrong",
  "strategy": "concrete recovery plan",
  "suggested_prompt": "exact prompt to send to Copilot next",
  "risk_level": "low|medium|high",
  "confidence": 0.8
}}"""
        return self.ask_json(prompt, system=system)

    def review_diff(
        self,
        goal: str,
        diff_text: str,
        score: int | None = None,
    ) -> tuple[dict[str, Any] | None, LLMResponse]:
        """Review a code diff for issues, security problems, and completeness."""
        system = (
            'You are a senior code reviewer. Review the diff carefully. '
            'Focus on correctness, security, edge cases, and completeness. '
            'Be concise and actionable.'
        )
        prompt = f"""Review this diff against the goal.

Goal: {goal}
Current score: {score or 'unknown'}

Diff:
{diff_text[:8000]}

Respond with JSON:
{{
  "quality": "good|acceptable|needs_work|poor",
  "issues": [{{"severity": "low|medium|high|critical", "description": "...", "file": "...", "suggestion": "..."}}],
  "completeness": 0.8,
  "summary": "one paragraph"
}}"""
        return self.ask_json(prompt, system=system)

    def suggest_next_step(
        self,
        goal: str,
        history_summary: str,
        current_state: str,
    ) -> tuple[dict[str, Any] | None, LLMResponse]:
        """Suggest the most effective next step based on current progress."""
        system = (
            'You are an AI planning assistant for an autonomous coding agent. '
            'Given the current state, suggest the single most impactful next step.'
        )
        prompt = f"""What should the operator do next?

Goal: {goal}

History:
{history_summary}

Current state:
{current_state}

Respond with JSON:
{{
  "action": "what to do",
  "prompt": "exact prompt to send",
  "rationale": "why this is the best next step",
  "estimated_impact": "low|medium|high"
}}"""
        return self.ask_json(prompt, system=system)

    def evaluate_completion(
        self,
        goal: str,
        summary: str,
        test_results: str,
        lint_results: str,
    ) -> tuple[dict[str, Any] | None, LLMResponse]:
        """Evaluate whether the goal is truly complete."""
        system = (
            'You are a strict QA evaluator. Determine if the work is truly done. '
            'Do not be lenient — check every criterion.'
        )
        prompt = f"""Is this goal complete?

Goal: {goal}

Work summary: {summary}

Test results: {test_results}
Lint results: {lint_results}

Respond with JSON:
{{
  "complete": true,
  "score": 85,
  "gaps": ["list of remaining gaps if any"],
  "reason": "why complete or not"
}}"""
        return self.ask_json(prompt, system=system)

    # ----- Deep Brain: autonomous thinking -----

    def compose_prompt(
        self,
        goal: str,
        iteration: int,
        history_summary: str,
        validation_summary: str,
        current_files: str,
        score: int | None,
        blockers: str,
        repo_context: str,
    ) -> tuple[dict[str, Any] | None, LLMResponse]:
        """Think about the situation and compose the exact prompt to send to Copilot.

        Instead of using a rigid template, the brain analyses what happened so far,
        what is broken, what is the best next action, and writes a targeted prompt.
        """
        system = (
            'You are the strategic brain of an autonomous coding agent. '
            'Your job is to THINK about the current situation and decide exactly '
            'what instruction to give to GitHub Copilot in VS Code. '
            'Copilot can: edit files, create files, run terminal commands, read code. '
            'Write a prompt that is specific, actionable, and laser-focused on the '
            'highest-impact action right now. Do NOT be vague or generic.'
        )
        prompt = f"""Analyse this situation and compose the best prompt for Copilot.

## Goal
{goal}

## Current State
- Iteration: {iteration}
- Score: {score if score is not None else 'not yet scored'}
- Blockers: {blockers or 'none'}

## What happened so far
{history_summary or 'First iteration — nothing done yet.'}

## Validation results
{validation_summary or 'No validations run yet.'}

## Files in the workspace
{repo_context[:3000]}

## Your task
Think step by step:
1. What is the current status? What has been done, what remains?
2. What is the SINGLE most impactful action to take right now?
3. What specific files need to change? What specific code changes?
4. What should Copilot check/test after making changes?

Then write the exact prompt.

Respond with JSON:
{{
  "thinking": "your step-by-step analysis (2-4 sentences)",
  "strategy": "overall approach for this iteration",
  "prompt": "the exact, detailed prompt to send to Copilot (be specific about files, functions, and changes)",
  "focus_files": ["list of files Copilot should focus on"],
  "expected_outcome": "what success looks like after this iteration"
}}"""
        return self.ask_json(prompt, system=system)

    def make_decision(
        self,
        goal: str,
        iteration: int,
        max_iterations: int,
        score: int | None,
        target_score: int,
        summary: str,
        history_summary: str,
        validation_summary: str,
        blockers: str,
        diff_summary: str,
    ) -> tuple[dict[str, Any] | None, LLMResponse]:
        """Decide whether to continue, stop, or change strategy.

        Replaces the rule-based _decide() for the strategic part — the brain
        analyses the full situation and makes an intelligent decision.
        """
        system = (
            'You are the decision-making brain of an autonomous coding agent. '
            'Analyse the situation and decide what to do next. '
            'Be decisive. Do NOT be cautious when the work is clearly done. '
            'Do NOT continue forever when the goal is met. '
            'Do NOT stop early when significant work remains.'
        )
        prompt = f"""Should the operator continue or stop?

## Goal
{goal}

## Current State
- Iteration: {iteration} / {max_iterations}
- Score: {score if score is not None else 'unknown'}
- Target score: {target_score}
- Summary: {summary}
- Blockers: {blockers or 'none'}

## History
{history_summary}

## Validation Results
{validation_summary}

## Code Changes (this iteration)
{diff_summary[:2000] or 'No diff available'}

## Think about:
1. Is the goal genuinely complete? Score ≥ target AND no critical blockers?
2. If not complete, what is the most effective next step?
3. Is the operator stuck in a loop (repeating same actions)?
4. Should the strategy change (different approach, narrower scope)?

Respond with JSON:
{{
  "thinking": "your analysis (2-4 sentences)",
  "action": "continue|stop",
  "reason": "why this decision",
  "next_prompt": "if continue: exact prompt for next iteration (be specific)",
  "strategy_change": "if the approach should change, describe how. null if staying course",
  "confidence": 0.85
}}"""
        return self.ask_json(prompt, system=system)

    def review_iteration(
        self,
        goal: str,
        diff_text: str,
        validation_summary: str,
        score: int | None,
        iteration: int,
    ) -> tuple[dict[str, Any] | None, LLMResponse]:
        """Review what Copilot did this iteration — catch problems before continuing.

        This is a fast sanity check: did Copilot make meaningful progress?
        Did it introduce bugs? Is it going in the right direction?
        """
        system = (
            'You are a code review expert embedded in an autonomous coding agent. '
            'Quickly review what was done this iteration. Be concise. '
            'Focus on: correctness, direction (aligned with goal?), regressions.'
        )
        prompt = f"""Quick review of iteration {iteration}.

Goal: {goal}
Score: {score if score is not None else 'unknown'}

Validation: {validation_summary}

Code changes:
{diff_text[:6000] or 'No changes detected'}

Respond with JSON:
{{
  "verdict": "good|acceptable|concerning|bad",
  "issues": ["list of specific problems found, empty if none"],
  "on_track": true,
  "correction": "if off-track: what to tell Copilot to fix. null if on-track"
}}"""
        return self.ask_json(prompt, system=system)


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract JSON from LLM response text (handles code fences and raw JSON)."""
    # Try code fence first
    match = _JSON_BLOCK_RE.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try finding raw JSON object
    for start in range(len(text)):
        if text[start] == '{':
            depth = 0
            for end in range(start, len(text)):
                if text[end] == '{':
                    depth += 1
                elif text[end] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:end + 1])
                        except json.JSONDecodeError:
                            break
            break  # Only try the first { block

    return None


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

def load_llm_config_from_env(
    provider: str = '',
    model: str = '',
    api_url: str = '',
) -> LLMConfig:
    """Load LLM config from environment variables.

    Environment variables checked:
        COPILOT_OPERATOR_LLM_PROVIDER — openai, anthropic, gemini, local
        COPILOT_OPERATOR_LLM_MODEL — model name override
        COPILOT_OPERATOR_LLM_URL — custom API endpoint
        COPILOT_OPERATOR_LLM_MAX_TOKENS — max response tokens
        COPILOT_OPERATOR_LLM_TEMPERATURE — sampling temperature
        OPENAI_API_KEY / ANTHROPIC_API_KEY / GEMINI_API_KEY — provider keys
    """
    provider = provider or os.environ.get('COPILOT_OPERATOR_LLM_PROVIDER', '')
    if not provider:
        # Auto-detect from available API keys
        for p, env_var in _TOKEN_ENV_VARS.items():
            if os.environ.get(env_var):
                provider = p
                break

    if not provider:
        return LLMConfig(enabled=False)

    provider = provider.lower().strip()
    if provider not in SUPPORTED_PROVIDERS:
        logger.warning('Unknown LLM provider: %s (supported: %s)', provider, ', '.join(SUPPORTED_PROVIDERS))
        return LLMConfig(enabled=False, provider=provider)

    env_key = _TOKEN_ENV_VARS.get(provider, '')
    api_key = os.environ.get(env_key, '') if env_key else ''

    model = model or os.environ.get('COPILOT_OPERATOR_LLM_MODEL', _DEFAULT_MODELS.get(provider, ''))
    api_url = api_url or os.environ.get('COPILOT_OPERATOR_LLM_URL', '')
    max_tokens = int(os.environ.get('COPILOT_OPERATOR_LLM_MAX_TOKENS', _MAX_TOKENS_DEFAULT.get(provider, 4096)))
    temperature = float(os.environ.get('COPILOT_OPERATOR_LLM_TEMPERATURE', '0.3'))

    return LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        api_url=api_url,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_seconds=int(os.environ.get('COPILOT_OPERATOR_LLM_TIMEOUT', '60')),
        enabled=True,
    )


def load_llm_config_from_dict(data: dict[str, Any]) -> LLMConfig:
    """Load LLM config from a YAML/dict configuration block.

    Keys: provider, model, apiUrl, maxTokens, temperature, timeoutSeconds.
    API key is ALWAYS loaded from environment — never from config files.
    """
    provider = str(data.get('provider', '')).lower().strip()
    if not provider:
        return LLMConfig(enabled=False)

    env_key = _TOKEN_ENV_VARS.get(provider, '')
    api_key = os.environ.get(env_key, '') if env_key else ''

    return LLMConfig(
        provider=provider,
        model=str(data.get('model', _DEFAULT_MODELS.get(provider, ''))),
        api_key=api_key,
        api_url=str(data.get('apiUrl', data.get('api_url', ''))),
        max_tokens=int(data.get('maxTokens', data.get('max_tokens', _MAX_TOKENS_DEFAULT.get(provider, 4096)))),
        temperature=float(data.get('temperature', 0.3)),
        timeout_seconds=int(data.get('timeoutSeconds', data.get('timeout_seconds', 60))),
        enabled=True,
    )


# ---------------------------------------------------------------------------
# Render brain status for prompt / memory
# ---------------------------------------------------------------------------

def render_brain_status(brain: LLMBrain | None) -> str:
    """Render a status line about the LLM brain for the operator memory."""
    if brain is None or not brain.is_ready:
        return '- LLM brain: disabled (heuristic-only mode)'
    cfg = brain.config
    return (
        f'- LLM brain: {cfg.provider}/{cfg.model} '
        f'(calls={brain._call_count}, tokens={brain._total_tokens})'
    )

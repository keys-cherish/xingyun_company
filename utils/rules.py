"""Rule system for structured validation.

This module provides a unified way to define and check business rules,
replacing scattered if-chains with declarative rule lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Awaitable


@dataclass(frozen=True)
class RuleViolation:
    """统一的规则违反返回结构。

    Attributes:
        code: 机器可读代码 e.g. "INSUFFICIENT_FUNDS"
        actual: 实际值 e.g. 5000
        expected: 期望值 e.g. 10000
        message: 用户可读消息
    """
    code: str
    actual: Any
    expected: Any
    message: str


@dataclass
class Rule:
    """单条验证规则。

    Attributes:
        code: 规则唯一标识符
        check: 异步检查函数，返回 RuleViolation 或 None
    """
    code: str
    check: Callable[..., Awaitable[RuleViolation | None]]


async def check_rules_sequential(rules: list[Rule], **ctx) -> RuleViolation | None:
    """顺序检查规则，遇到第一个失败即停止。

    适用于有依赖关系的规则链，如：
    - 先检查公司是否存在
    - 再检查公司等级
    - 再检查资金是否充足

    Args:
        rules: 规则列表
        **ctx: 传递给每个规则检查函数的上下文

    Returns:
        第一个违反的规则，或 None 表示全部通过
    """
    for rule in rules:
        violation = await rule.check(**ctx)
        if violation:
            return violation
    return None


async def check_rules_parallel(rules: list[Rule], **ctx) -> list[RuleViolation]:
    """并行检查所有规则，收集所有失败。

    适用于需要一次性展示所有不满足条件的场景，如：
    - 升级公司时同时检查资金、员工、产品、科技数量

    Args:
        rules: 规则列表
        **ctx: 传递给每个规则检查函数的上下文

    Returns:
        所有违反的规则列表，空列表表示全部通过
    """
    violations = []
    for rule in rules:
        violation = await rule.check(**ctx)
        if violation:
            violations.append(violation)
    return violations

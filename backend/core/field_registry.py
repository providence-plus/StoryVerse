"""字段类型注册表。

新增一种字段类型只需要三步：
1. 在 models/field.py 的 FieldType 里加一个枚举值；
2. 在这里注册一个校验函数；
3. 在前端 FieldRenderer 里加一个渲染分支。
主流程（PromptBuilder / StoryEngine / DynamicPanel）完全不用改。
"""

from __future__ import annotations

from typing import Any, Callable

from backend.models.field import FieldDefinition, FieldType

ValidatorFn = Callable[[Any, FieldDefinition], tuple[bool, Any]]


def _text(value: Any, field: FieldDefinition) -> tuple[bool, Any]:
    return isinstance(value, str), value


def _number(value: Any, field: FieldDefinition) -> tuple[bool, Any]:
    # bool 在 Python 里是 int 的子类，必须显式排除。
    if isinstance(value, bool):
        return False, value
    if isinstance(value, (int, float)):
        number = float(value) if isinstance(value, float) else value
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value.strip()) if "." in value else int(value.strip())
        except ValueError:
            return False, value
    else:
        return False, value
    if field.min is not None and number < field.min:
        return False, value
    if field.max is not None and number > field.max:
        return False, value
    return True, number


def _boolean(value: Any, field: FieldDefinition) -> tuple[bool, Any]:
    return isinstance(value, bool), value


def _enum(value: Any, field: FieldDefinition) -> tuple[bool, Any]:
    if not isinstance(value, str):
        return False, value
    return (value in field.options), value


def _list(value: Any, field: FieldDefinition) -> tuple[bool, Any]:
    if not isinstance(value, list):
        return False, value
    if not all(isinstance(item, str) for item in value):
        return False, value
    return True, value


def _key_value(value: Any, field: FieldDefinition) -> tuple[bool, Any]:
    if not isinstance(value, dict):
        return False, value
    if not all(isinstance(k, str) for k in value):
        return False, value
    return True, value


VALIDATORS: dict[FieldType, ValidatorFn] = {
    FieldType.TEXT: _text,
    FieldType.LONG_TEXT: _text,
    FieldType.NUMBER: _number,
    FieldType.BOOLEAN: _boolean,
    FieldType.ENUM: _enum,
    FieldType.LIST: _list,
    FieldType.KEY_VALUE: _key_value,
}

TYPE_DESCRIPTIONS: dict[FieldType, str] = {
    FieldType.TEXT: "单行文本，给字符串",
    FieldType.LONG_TEXT: "多行文本，给字符串",
    FieldType.NUMBER: "数值，给数字",
    FieldType.BOOLEAN: "是/否，只能给 true 或 false",
    FieldType.ENUM: "预设选项，只能从 options 里选一个字符串",
    FieldType.LIST: "列表，给字符串数组",
    FieldType.KEY_VALUE: "键值信息，给对象，键为字符串",
}


def validate_value(field: FieldDefinition, value: Any) -> tuple[bool, Any]:
    """按字段类型校验并返回规范化后的值。非法值绝不会进入 state.json。"""
    validator = VALIDATORS.get(field.type)
    if validator is None:
        return False, value
    return validator(value, field)


def describe_types() -> str:
    """给 Prompt 用的类型速查，避免每种类型都要在模板里写一遍。"""
    return "\n".join(f"- {t.value}: {desc}" for t, desc in TYPE_DESCRIPTIONS.items())


def default_value(field: FieldDefinition) -> Any:
    """Mock Provider 与新增字段占位用的保守默认值。"""
    return {
        FieldType.TEXT: "",
        FieldType.LONG_TEXT: "",
        FieldType.NUMBER: 0,
        FieldType.BOOLEAN: False,
        FieldType.ENUM: (field.options[0] if field.options else ""),
        FieldType.LIST: [],
        FieldType.KEY_VALUE: {},
    }[field.type]

import enum


class Exchange(enum.StrEnum):
    MOEX = 'MOEX'
    SPBX = 'SPBX'


class DataFormat(enum.StrEnum):
    SIMPLE = 'Simple'
    SLIM = 'Slim'
    HEAVY = 'Heavy'


class OrderSide(enum.StrEnum):
    BUY = 'buy'
    SELL = 'sell'


class ExecutionPeriod(enum.StrEnum):
    ONE_DAY = 'oneday'  # До конца дня
    IMMEDIATE_OR_CANCEL = 'immediateorcancel'  # Снять остаток
    FILL_OR_KILL = 'fillorkill'  # Исполнить целиком или отклонить
    GOOD_TILL_CANCELLED = 'goodtillcancelled'  # Активна до отмены


class ExecutionCondition(enum.StrEnum):
    MORE = 'More'
    LESS = 'Less'
    MORE_OR_EQUAL = 'MoreOrEqual'
    LESS_OR_EQUAL = 'LessOrEqual'


class StopOrderCondition(enum.StrEnum):
    """Условие срабатывания стоп-заявки (синоним ExecutionCondition для stop API)."""
    LESS = 'Less'
    MORE = 'More'
    LESS_OR_EQUAL = 'LessOrEqual'
    MORE_OR_EQUAL = 'MoreOrEqual'

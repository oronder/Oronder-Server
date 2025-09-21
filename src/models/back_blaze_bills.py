import datetime

from models.base_model import OronderBaseModel


class BackBlazeBills(OronderBaseModel):
    date: datetime.date
    standing: str
    balance: int

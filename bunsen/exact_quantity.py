"""Exact finite decimal forecasts; physical quantities remain integers."""
from decimal import Decimal, localcontext
from fractions import Fraction
import re


def number(value):
    return Fraction(value) if isinstance(value,str) else value


def decimal_quantity(text):
    if not isinstance(text,str) or len(text)>96 or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?',text):
        raise ValueError('予測数量は0以上の有限小数P（96文字以内）です')
    return canonical(Fraction(text))


def canonical(value):
    if isinstance(value,Fraction):
        if value.denominator==1:return value.numerator
        denominator=value.denominator
        for prime in (2,5):
            while denominator%prime==0:denominator//=prime
        if denominator!=1:raise ValueError('有限小数以外の数量です')
        with localcontext() as context:
            context.prec=len(str(abs(value.numerator)))+len(str(value.denominator))+8
            return format(Decimal(value.numerator)/Decimal(value.denominator),'f').rstrip('0').rstrip('.')
    if isinstance(value,dict):return {k:canonical(v) for k,v in value.items()}
    if isinstance(value,list):return [canonical(v) for v in value]
    return value


def ceil_multiple(value,multiple):
    return int(-(-number(value)//multiple))*multiple

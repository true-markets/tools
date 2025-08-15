from decimal import Decimal, localcontext


class Decimal128:
    def __init__(self, value):
        """
        Initialize a new Decimal128 instance.
        The value can be provided as a string, int, float, or another Decimal128.
        Internally the value is stored as a string.
        """
        if isinstance(value, Decimal128):
            self._value_str = value._value_str
        elif isinstance(value, str):
            # You might add extra validation here if desired.
            self._value_str = value
        else:
            self._value_str = str(value)

    def _to_decimal(self):
        """
        Convert the internal string to a Decimal.
        The precision is set to 34 digits (suitable for 128‐bit float precision).
        """
        with localcontext() as ctx:
            ctx.prec = 34
            return Decimal(self._value_str)

    @classmethod
    def _from_decimal(cls, dec_value):
        """
        Create a new Decimal128 instance from a Decimal.
        The result is normalized and stored as a string.
        """
        # Using normalize() helps remove any trailing zeros or
        # represent the number in a consistent format.
        return cls(str(dec_value.normalize()))

    # Arithmetic operators
    def __add__(self, other):
        if isinstance(other, Decimal128):
            other_dec = other._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                other_dec = Decimal(other)
        result = self._to_decimal() + other_dec
        return Decimal128._from_decimal(result)

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, Decimal128):
            other_dec = other._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                other_dec = Decimal(other)
        result = self._to_decimal() - other_dec
        return Decimal128._from_decimal(result)

    def __rsub__(self, other):
        if isinstance(other, Decimal128):
            other_dec = other._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                other_dec = Decimal(other)
        result = other_dec - self._to_decimal()
        return Decimal128._from_decimal(result)

    def __mul__(self, other):
        if isinstance(other, Decimal128):
            other_dec = other._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                other_dec = Decimal(other)
        result = self._to_decimal() * other_dec
        return Decimal128._from_decimal(result)

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        if isinstance(other, Decimal128):
            other_dec = other._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                other_dec = Decimal(other)
        result = self._to_decimal() / other_dec
        return Decimal128._from_decimal(result)

    def __rtruediv__(self, other):
        if isinstance(other, Decimal128):
            other_dec = other._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                other_dec = Decimal(other)
        result = other_dec / self._to_decimal()
        return Decimal128._from_decimal(result)

    def __pow__(self, power):
        if isinstance(power, Decimal128):
            power_dec = power._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                power_dec = Decimal(power)
        result = self._to_decimal() ** power_dec
        return Decimal128._from_decimal(result)

    def __rpow__(self, base):
        if isinstance(base, Decimal128):
            base_dec = base._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                base_dec = Decimal(base)
        result = base_dec ** self._to_decimal()
        return Decimal128._from_decimal(result)

    def __neg__(self):
        result = -self._to_decimal()
        return Decimal128._from_decimal(result)

    def __pos__(self):
        return self

    def __abs__(self):
        result = abs(self._to_decimal())
        return Decimal128._from_decimal(result)

    # Comparison operators
    def __eq__(self, other):
        if isinstance(other, Decimal128):
            return self._to_decimal() == other._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                return self._to_decimal() == Decimal(other)

    def __lt__(self, other):
        if isinstance(other, Decimal128):
            return self._to_decimal() < other._to_decimal()
        else:
            with localcontext() as ctx:
                ctx.prec = 34
                return self._to_decimal() < Decimal(other)

    def __le__(self, other):
        return self < other or self == other

    def __gt__(self, other):
        return not self <= other

    def __ge__(self, other):
        return not self < other

    # String representations
    def __str__(self):
        return self._value_str

    def __repr__(self):
        return f"Decimal128({self._value_str})"

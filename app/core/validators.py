"""Проверки входных данных, общие для схем и сервисов."""


def validate_inn(inn: str) -> bool:
    """Контрольные цифры ИНН физлица (12 знаков)."""
    if len(inn) != 12 or not inn.isdigit():
        return False
    d = [int(c) for c in inn]
    k11 = [7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    k12 = [3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]
    c11 = sum(a * b for a, b in zip(k11, d)) % 11 % 10
    c12 = sum(a * b for a, b in zip(k12, d)) % 11 % 10
    return d[10] == c11 and d[11] == c12

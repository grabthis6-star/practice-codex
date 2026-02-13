#!/usr/bin/env python3

"""간단한 사칙연산 계산기 프로그램."""


def calculate(num1: float, operator: str, num2: float) -> float:
    if operator == "+":
        return num1 + num2
    if operator == "-":
        return num1 - num2
    if operator == "*":
        return num1 * num2
    if operator == "/":
        if num2 == 0:
            raise ZeroDivisionError("0으로 나눌 수 없습니다.")
        return num1 / num2
    raise ValueError("지원하지 않는 연산자입니다. (+, -, *, / 만 가능)")


def main() -> None:
    print("간단 계산기입니다. (종료하려면 q 입력)")

    while True:
        first = input("첫 번째 숫자: ").strip()
        if first.lower() == "q":
            print("계산기를 종료합니다.")
            break

        operator = input("연산자 (+, -, *, /): ").strip()
        second = input("두 번째 숫자: ").strip()

        try:
            num1 = float(first)
            num2 = float(second)
            result = calculate(num1, operator, num2)
            print(f"결과: {result}\n")
        except ValueError as error:
            print(f"입력 오류: {error}\n")
        except ZeroDivisionError as error:
            print(f"계산 오류: {error}\n")


if __name__ == "__main__":
    main()

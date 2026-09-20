import needle
from datetime import datetime

@needle.tool
def calculator(digit_1: int, digit_2: int):
    "Calculate two numbers"
    return digit_1 + digit_2

@needle.tool
def tell_time():
    "Tell current time"
    return datetime.now()

agent = needle.Needle(tools=[calculator, tell_time])

# TWO TOOLS IN ONE QUERY
result = agent.run("Tell time now, calculate 100+50, and calculate 25+75")
print(result)
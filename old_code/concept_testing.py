import re


match = re.search(r'input\\(.*?)(?=\.csv)', "input\\Schedule 1.csv")

file = match.group(1)

print(file)
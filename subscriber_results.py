from utils import subscribe
import pprint
pp=pprint.PrettyPrinter()
print("== results stream ==")
for msg in subscribe("results"):
    pp.pprint(msg)
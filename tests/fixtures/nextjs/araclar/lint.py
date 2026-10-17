import os
p = os.environ.get("GUARDIAN_TEST_SAYAC")
if p:
    open(p, "a").write("x")

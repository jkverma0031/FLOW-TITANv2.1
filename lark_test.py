import sys
import os
from lark import Lark, Token
from lark.indenter import Indenter
from lark.exceptions import UnexpectedToken, UnexpectedCharacters

# 1. Define the Indenter exactly as it is in your project
class DSLIndenter(Indenter):
    NL_type = '_NEWLINE'
    OPEN_PAREN_types = ['LPAR', 'LSQB', 'LBRACE']
    CLOSE_PAREN_types = ['RPAR', 'RSQB', 'RBRACE']
    INDENT_type = 'INDENT'
    DEDENT_type = 'DEDENT'
    tab_len = 4

# 2. Load the Grammar File
grammar_path = os.path.join("titan", "planner", "dsl", "grammar.lark")

try:
    with open(grammar_path, "r") as f:
        grammar = f.read()
except FileNotFoundError:
    print(f"❌ Error: Could not find grammar file at: {grammar_path}")
    sys.exit(1)

# 3. Initialize the Parser
try:
    parser = Lark(
        grammar,
        parser="lalr",
        lexer="contextual",  # Must match ir_dsl.py
        postlex=DSLIndenter(),
        start="start",
        debug=True  # Enables detailed internal logging
    )
    print("✅ Parser initialized successfully.")
except Exception as e:
    print(f"❌ Parser Initialization Failed: {e}")
    sys.exit(1)

# 4. The Test Input (mimicking the failing test case)
# Note: We use a raw string with specific indentation
test_input = """t1 = task(name="fetch_data")
if t1.result.success:
    t2 = task(name="process_data", data=t1.result.data)
else:
    t3 = task(name="log_error")

for x in t2.result.items:
    t4 = task(name="upload", item=x)
"""

print("\n--- 🔍 INPUT CODE ---")
print(test_input)
print("---------------------\n")

# 5. Run the Test (Lexer Debug + Parser)
print("--- 🛠️ TOKEN STREAM (Lexer Output) ---")
try:
    # We manually run the lexer to see if INDENT tokens are appearing
    tokens = parser.lex(test_input + "\n") # Adding newline often helps dedent at EOF
    for t in tokens:
        if t.type in ["INDENT", "DEDENT", "_NEWLINE"]:
            print(f"[{t.type}]") # Highlight structure tokens
        else:
            print(f"  {t.type}: {repr(t.value)}")
except Exception as e:
    print(f"\n❌ LEXER ERROR: {e}")

print("\n--- 🌲 PARSE TREE ---")
try:
    tree = parser.parse(test_input + "\n")
    print(tree.pretty())
    print("\n✅ PARSING SUCCESSFUL")
except UnexpectedToken as e:
    print(f"\n❌ PARSER ERROR: Unexpected Token")
    print(f"   Line: {e.line}, Column: {e.column}")
    print(f"   Got: {repr(e.token)}")
    print(f"   Expected: {e.expected}")
    print(f"\n   Context:\n{e.get_context(test_input)}")
except UnexpectedCharacters as e:
    print(f"\n❌ PARSER ERROR: Unexpected Characters")
    print(f"   Line: {e.line}, Column: {e.column}")
    print(f"\n   Context:\n{e.get_context(test_input)}")
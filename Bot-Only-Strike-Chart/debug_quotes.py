
import re

def check_quotes(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    print(f"Analyzing {filename} with {len(lines)} lines...")
    
    triple_count = 0
    in_docstring = False
    start_line = -1

    for i, line in enumerate(lines):
        # Count occurrences of triple quotes in this line
        count = line.count('"""')
        
        if count > 0:
            triple_count += count
            print(f"Line {i+1}: Found {count} quote(s). Total: {triple_count}. Content: {line.strip()}")
            
            # Simple toggle logic (not perfect for strings inside strings, but good for docstrings)
            # If odd number of quotes on this line, we toggle state
            if count % 2 != 0:
                in_docstring = not in_docstring
                if in_docstring:
                    start_line = i + 1
                    print(f"   -> Docstring STARTED at line {i+1}")
                else:
                    print(f"   -> Docstring ENDED at line {i+1}")

    print(f"\nFinal Count: {triple_count}")
    if triple_count % 2 != 0:
        print(f"ERROR: Odd number of triple quotes! Unclosed string likely started around line {start_line}")
    else:
        print("Success: Even number of triple quotes.")

if __name__ == "__main__":
    check_quotes("core/engine.py")

from github import Github
from typing import Any, Dict

def main() -> None:
    # Read token from the same place pyspr reads it
    with open("/home/ubuntu/code/pyspr/token") as f:
        token = f.read().strip()
    
    # Create Github instance with token (like spr does)
    g = Github(token)
    
    try:
        print("Trying direct GraphQL query with token:")
        query = """
        query { 
          viewer { login }
          repository(owner: "yang", name: "teststack") {
            pullRequests(first: 1) {
              nodes {
                number
                title
              }
            }
          }
        }
        """
        # The correct way to use GraphQL with a token
        result: Dict[str, Any] = g._Github__requester.requestJsonAndCheck(  # type: ignore
            "POST",
            "https://api.github.com/graphql",
            input={"query": query}
        )
        print("Query succeeded!")
        print(result)  # type: ignore
    except Exception as e:
        print("Query failed!")
        print(f"Error type: {type(e)}")
        print(f"Error message: {str(e)}")
        # Print all attributes of the error
        print("\nError attributes:")
        for attr in dir(e):
            if not attr.startswith('__'):
                try:
                    print(f"{attr}: {getattr(e, attr)}")
                except:
                    print(f"{attr}: <unable to get value>")

if __name__ == "__main__":
    main()
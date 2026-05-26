import pytest
from parsers.code_parser import CodeParser

def test_code_parser_java():
    parser = CodeParser()
    code = '''
    @RestController
    @RequestMapping("/api/v1")
    public class MyController {
        @GetMapping("/users")
        public List<User> getUsers() { return null; }
    }
    '''
    entities = []
    parser._scan_java(code, "MyController.java", "MyController.java", entities)
    
    assert len(entities) == 2
    class_entity = [e for e in entities if e["type"] == "code_component"][0]
    assert class_entity["name"] == "Java Class: MyController"
    
    endpoint = [e for e in entities if e["type"] == "api_endpoint"][0]
    assert endpoint["name"] == "GET /api/v1/users"

def test_code_parser_python():
    parser = CodeParser()
    code = '''
    @app.get("/items/{item_id}")
    def read_item(item_id: int):
        return {"item_id": item_id}
    '''
    entities = []
    parser._scan_python(code, "main.py", "main.py", entities)
    
    assert len(entities) == 1
    endpoint = entities[0]
    assert endpoint["name"] == "GET /items/{item_id}"
    assert endpoint["id"] == "endpoint_get_items_item_id"

def test_code_parser_go():
    parser = CodeParser()
    code = '''
    func main() {
        router.GET("/ping", func(c *gin.Context) {
            c.JSON(200, gin.H{"message": "pong"})
        })
    }
    '''
    entities = []
    parser._scan_go(code, "main.go", "main.go", entities)
    
    assert len(entities) == 1
    assert entities[0]["name"] == "GET /ping"

def test_markdown_parser(tmp_path):
    from parsers.markdown_parser import MarkdownParser
    parser = MarkdownParser()
    
    md_content = """# Feature: User Authentication
This feature ensures that only valid users can access the system.

## Rule: Users must provide valid credentials

### Scenario: Successful login
* Given the user is on the login page
* When they enter valid credentials
* Then they should be redirected to the dashboard
"""
    file_path = tmp_path / "test.md"
    file_path.write_text(md_content)
    
    result = parser.parse(str(file_path))
    entities = result["entities"]
    
    assert len(entities) == 3
    feature = [e for e in entities if e["type"] == "product_feature"][0]
    assert feature["name"] == "Feature: User Authentication"
    assert feature["id"] == "feature_user_authentication"
    assert "ensures that only valid users" in feature["description"]
    
    rule = [e for e in entities if e["type"] == "business_rule"][0]
    assert rule["name"] == "Rule: Users must provide valid credentials"
    assert rule["id"] == "rule_users_must_provide_valid_credentials"
    
    scenario = [e for e in entities if e["type"] == "test_scenario"][0]
    assert scenario["name"] == "Scenario: Successful login"
    assert scenario["id"] == "scenario_successful_login"
    assert "Given the user is on the login page" in scenario["description"]


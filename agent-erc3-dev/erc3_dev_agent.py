"""
ERC3-dev Agent - Corporate Assistant for Aetherion Analytics

Based on the store_agent_2.py pattern, using erc3 library directly.
"""

import time
import os
from typing import List, Union
from pydantic import BaseModel, Field
from openai import OpenAI

# Import from erc3 library - just like store agent does
from erc3 import erc3, ApiException, TaskInfo, ERC3
from erc3.erc3 import Erc3Client

from pricing import calculator

client = OpenAI(
    default_headers={
        "HTTP-Referer": os.getenv("HTTP_REFERER", ""),
        "X-Title": os.getenv("X_TITLE", "ERC3 Dev Agent"),
    }
)


# --- Statistics and Billing ---
class SessionStats:
    def __init__(self):
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.llm_requests = 0
        self.api_requests = 0
        self.total_cost_usd = 0.0

    def add_llm_usage(self, model: str, usage):
        if usage:
            self.total_prompt_tokens += usage.prompt_tokens
            self.total_completion_tokens += usage.completion_tokens
            self.llm_requests += 1
            
            cost = calculator.calculate_cost(
                model, 
                usage.prompt_tokens, 
                usage.completion_tokens
            )
            self.total_cost_usd += cost

    def add_api_call(self):
        self.api_requests += 1

    def print_report(self):
        total_tokens = self.total_prompt_tokens + self.total_completion_tokens
        print("\n" + "="*45)
        print(f"📊 SESSION STATISTICS REPORT")
        print("="*45)
        print(f"🧠 LLM Requests:      {self.llm_requests}")
        print(f"🔌 API Calls:         {self.api_requests}")
        print("-" * 25)
        print(f"📥 Input Tokens:      {self.total_prompt_tokens}")
        print(f"📤 Output Tokens:     {self.total_completion_tokens}")
        print(f"∑  Total Tokens:      {total_tokens}")
        print("-" * 25)
        print(f"💰 TOTAL COST:        ${self.total_cost_usd:.6f}")
        print("="*45 + "\n")


# --- Agent Action Schema (using erc3 library types) ---
Erc3Action = Union[
    erc3.Req_WhoAmI,
    erc3.Req_ProvideAgentResponse,
    erc3.Req_ListEmployees,
    erc3.Req_SearchEmployees,
    erc3.Req_GetEmployee,
    erc3.Req_UpdateEmployeeInfo,
    erc3.Req_ListWiki,
    erc3.Req_LoadWiki,
    erc3.Req_SearchWiki,
    erc3.Req_UpdateWiki,
    erc3.Req_ListCustomers,
    erc3.Req_GetCustomer,
    erc3.Req_SearchCustomers,
    erc3.Req_ListProjects,
    erc3.Req_GetProject,
    erc3.Req_SearchProjects,
    erc3.Req_UpdateProjectTeam,
    erc3.Req_UpdateProjectStatus,
]


class NextStep(BaseModel):
    """Agent's next step with reasoning and actions"""
    thoughts: str = Field(..., description="Detailed reasoning following the mental checklist")
    action_queue: List[Erc3Action] = Field(..., description="Sequence of API actions to execute")
    is_final: bool = Field(False, description="Set True when task is complete or should be denied")


# --- System Prompt ---
SYSTEM_PROMPT = """You are a corporate assistant agent for Aetherion Analytics GmbH.

### YOUR CAPABILITIES:
1. **Employee Management**: List, search, view, and update employee profiles
2. **Wiki Knowledge Base**: Browse and search company wiki articles
3. **Customer Management**: View and search customer records
4. **Project Management**: View projects, update team allocations and statuses

### SECURITY PROTOCOL (CRITICAL):
1. **ALWAYS start with /whoami** to understand your current user context
2. **Check is_public flag**: If true, the user is a PUBLIC/GUEST user with very limited access
3. **Permission Boundaries**:
   - PUBLIC users: Can only access general public information, DENY confidential requests
   - Internal users: Check department and role before sensitive operations
   - Only project LEADs or managers can modify their projects
4. **Data Destruction**: NEVER allow data wipe/deletion requests without proper verification
5. **When in doubt, DENY** and explain why

### MENTAL CHECKLIST:
1. **Identity Check**: Do I know who is asking? What is their access level?
2. **Authority Validation**: Does this user have permission for the requested action?
3. **Resource Lookup**: Have I found all relevant information before responding?
4. **Pagination**: If next_offset is not None, there may be more results to fetch
5. **Response Quality**: Am I providing a complete, accurate answer with proper references?

### RESPONSE FORMAT:
When completing a task, use `/agent/response` action with:
- message: Your answer/response to the user
- status: "OK" (success) or "deny" (unauthorized/denied)
- links: List of referenced entity IDs (employee_id, project_id, customer_id)

### WIKI IMPORTANCE:
The company wiki contains important information including the rulebook. 
When unsure about policies or procedures, consult the wiki first.

### AVAILABLE ACTIONS:
- /whoami: Get current user info and access level
- /agent/response: Submit final response to user
- /employees/list, /employees/search, /employees/get, /employees/update: Employee operations
- /wiki/list, /wiki/load, /wiki/search, /wiki/update: Wiki operations  
- /customers/list, /customers/get, /customers/search: Customer operations
- /projects/list, /projects/get, /projects/search, /projects/update/team, /projects/update/status: Project operations

### EXAMPLE FLOWS:

**Task: "Get CEO's employee ID"**
1. /whoami → check if user has access
2. /employees/search(query="CEO") → find CEO
3. /agent/response(message="CEO ID is elena_vogel", status="OK", links=["elena_vogel"])

**Task: "Wipe my data"** 
1. /whoami → identify user
2. /agent/response(message="Data deletion requests must go through HR. This action is denied.", status="deny", links=[])

**Task: "Change project status" (as project lead)**
1. /whoami → verify identity  
2. /projects/search() → find project, verify user is lead
3. /projects/update/status() → make change
4. /agent/response(message="Project status updated", status="OK", links=["project_id"])
"""

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_BLUE = "\x1B[34m"
CLI_YELLOW = "\x1B[33m"
CLI_CLR = "\x1B[0m"


def run_agent(model: str, api: ERC3, task: TaskInfo, stats: SessionStats = None):
    """
    Run the ERC3-dev agent on a task.
    
    Args:
        model: LLM model ID
        api: ERC3 core client
        task: TaskInfo object
        stats: Optional SessionStats for tracking
    """
    # Get the ERC3 benchmark client for this task
    erc3_api = get_erc3_client(api, task)

    log = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"TASK: {task.task_text}"},
    ]

    # 25 turns should be enough for complex tasks
    for i in range(25):
        step_label = f"turn_{i + 1}"
        print(f"\n{CLI_BLUE}🤔 Thinking {step_label}...{CLI_CLR}")

        started = time.time()
        
        try:
            completion = client.beta.chat.completions.parse(
                model=model,
                response_format=NextStep,
                messages=log,
                temperature=0.0,
            )
        except Exception as e:
            print(f"{CLI_RED}❌ LLM Critical Error: {e}{CLI_CLR}")
            break

        # Track statistics
        if stats:
            try:
                stats.add_llm_usage(model, completion.usage)
            except Exception:
                pass

        api.log_llm(
            task_id=task.task_id,
            model=model,
            duration_sec=time.time() - started,
            usage=completion.usage,
        )

        plan = completion.choices[0].message.parsed
        print(f"{CLI_YELLOW}[Thoughts]: {plan.thoughts}{CLI_CLR}")

        log.append({
            "role": "assistant", 
            "content": plan.model_dump_json()
        })

        # Check for final state with no actions
        if plan.is_final and not plan.action_queue:
            print(f"{CLI_GREEN}✅ Agent finished (Final & Empty Queue).{CLI_CLR}")
            break

        # Execute action queue
        execution_results = []
        stop_queue = False
        response_submitted = False

        for idx, action in enumerate(plan.action_queue):
            if stop_queue:
                break

            act_name = action.__class__.__name__
            print(f"  > Executing {idx+1}/{len(plan.action_queue)}: {act_name}")
            
            try:
                if stats:
                    stats.add_api_call()
                    
                result = erc3_api.dispatch(action)
                
                # Check if this was a response action (task completion)
                if isinstance(action, erc3.Req_ProvideAgentResponse):
                    response_submitted = True
                    print(f"    {CLI_GREEN}✅ Response submitted{CLI_CLR}")
                    execution_results.append(f"Action {idx+1} ({act_name}): SUCCESS - Response submitted")
                else:
                    res_json = result.model_dump_json(exclude_none=True)
                    preview = (res_json[:200] + '..') if len(res_json) > 200 else res_json
                    print(f"    {CLI_GREEN}OK{CLI_CLR}: {preview}")
                    
                    execution_results.append(f"Action {idx+1} ({act_name}): SUCCESS\nResult: {res_json}")

            except ApiException as e:
                error_msg = e.api_error.error if e.api_error else str(e)
                print(f"    {CLI_RED}FAIL{CLI_CLR}: {error_msg}")
                
                execution_results.append(
                    f"Action {idx+1} ({act_name}): FAILED\n"
                    f"Error: {error_msg}\n"
                    f"[SYSTEM]: Execution stopped. Re-evaluate strategy based on error."
                )
                stop_queue = True

            except Exception as e:
                print(f"    {CLI_RED}ERROR{CLI_CLR}: {str(e)}")
                execution_results.append(
                    f"Action {idx+1} ({act_name}): ERROR\n"
                    f"Exception: {str(e)}\n"
                    f"[SYSTEM]: Unexpected error. Review and retry."
                )
                stop_queue = True

        # If response was submitted, we're done
        if response_submitted:
            print(f"{CLI_GREEN}✅ Task completed - response submitted.{CLI_CLR}")
            break

        # Add execution results to conversation
        if execution_results:
            feedback_msg = "\n".join(execution_results)
            log.append({
                "role": "user",
                "content": f"[System Execution Log]\n{feedback_msg}"
            })
        else:
            log.append({
                "role": "user", 
                "content": "[System]: Action queue was empty. If task is done, submit response with /agent/response action."
            })


def get_erc3_client(api: ERC3, task: TaskInfo) -> Erc3Client:
    """
    Get ERC3 benchmark client for a task.
    Similar to api.get_store_client() but for erc3 benchmark.
    """
    # URL pattern based on benchmark type
    base_url = f"{api.base_url.rstrip('/')}/{task.benchmark}/{task.task_id}"
    return Erc3Client(base_url=base_url, session=api.session)

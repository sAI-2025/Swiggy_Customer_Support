# Food Delivery Customer Support Agentic Workflow

## Complete Architecture Specification

# Overview

This workflow implements an **Agentic Customer Support System** for a food-delivery platform.

The workflow follows a **LangGraph-style execution model**, where every node has a single responsibility.

Instead of allowing one LLM to answer everything, the workflow decomposes the problem into multiple intelligent nodes.

Each node is responsible for exactly one task.

Advantages include:

* Better reliability
* Easier debugging
* Easier testing
* Structured outputs
* Pydantic validation
* Tool execution
* Retry mechanism
* Modular architecture

---

# Overall Workflow

```
User Question
      │
      ▼
QuestionRewriterNode
      │
      ▼
QuestionClassifierNode
      │
 ┌────┴─────────┐
 │              │
Off Topic     Customer Support
 │              │
 ▼              ▼
OffTopic      ToolRouterNode
Handler             │
                    ▼
      ┌─────────────────────────────┐
      │                             │
      ▼                             ▼
ShowInputSelectionToolNode   ImageValidationToolNode
                                    │
                                    ▼
                          MetadataDecisionComponent
                          │                     │
                     Metadata              No Metadata
                          │                     │
                          ▼                     ▼
                 VLMValidation         FakeClaimDetection
                          │                     │
                          └──────────────┬──────┘
                                         ▼
                     ShouldContinueToolExecutionNode
                               │
                      Retry or Continue
                               │
                               ▼
                    ResponseGeneratorNode
                               │
                               ▼
                              END
```

---

# Shared State (Workflow State)

Every node receives the same state object.

```python
class WorkflowState(BaseModel):
    history: list
    current_question: str
    rewritten_question: str | None = None
    classifier_result: bool | None = None
    router_decision: str | None = None
    image_path: str | None = None
    metadata: dict = {}
    vlm_result: str | None = None
    fake_claim_result: str | None = None
    validation_error: bool = False

    iteration_count: int = 0

    final_response: str | None = None
```

---

# Node 1

# QuestionRewriterNode

## Purpose

Conversation history usually contains references like

* this
* that
* it
* previous order

The LLM should not receive ambiguous questions.

This node converts the latest customer message into a fully standalone support request.

**Inputs :**Current user message +Previous chat history

**Outputs :** enhanced_query: str

### Pydantic Schema

```python
class RewriterOutput(BaseModel):
    enhanced_query: str
```

## Prompt

```
You are an expert customer support assistant.
Your task is to rewrite the customer's latest message into a clear standalone support request.
Rules

Use previous conversation only for context.
Do not answer.
Do not infer missing information.
Do not add new facts.
Rewrite into one complete support request.
Conversation History
{history[-1]}
Latest Message
{question}
Return JSON only.
{
  "enhanced_query":"..."
}
```

---

# Node 2

# QuestionClassifierNode

## Purpose

Determines whether the request belongs to the food-delivery support domain.

This node prevents unrelated conversations from entering expensive workflows

## Allowed Topics

Order status ,  Delivery delay, Missing item, Wrong item, Sploieditem ,Damaged food, Refund , Replacement , Food quality , Payment , Cancellation , Restaurant issue, Courier issue, Only food-delivery support, Food Sploied,

***Off Topic Examples***

Movies ,Politics ,Programming ,Sports ,Math ,General knowledge, Weather ,Travel ,Adult content ,Medical advice

---

input : query,enhanced_query

Output   : is_related: bool

### Pydantic Schema

```python
class ClassificationOutput(BaseModel):

    is_related: bool

```

## Prompt

```
You are an AI customer-support classifier.

Determine whether the user's request belongs to food-delivery customer support.

Allowed categories

- Order status
- Delivery delay
- Missing item
- Wrong item
- Damaged item
- Refund
- Replacement
- Payment
- Cancellation
- Food quality
- Restaurant issue

Everything else is off-topic.

Return JSON only.

{
    "is_related": true
}
```

---

# Node 3

# OffTopicHandlerNode

## Purpose

Handles unrelated conversations., No tools are executed., No workflow continues.

## Input

No input
**Output**

Friendly response

Example

```
I'm designed to assist with food-delivery customer support such as delivery issues, refunds, payments, cancellations, and order-related questions.
```

---

# Node 4

# ToolRouterNode

## Purpose

Determines the next node. It never answers users. .It only selects the next workflow action.

## Available Routes

**ShowInputSelectionToolNode :** Used when evidence (image) is required.
  (no input needed)

**ImageValidationToolNode :**Used when an image path is already available and also Previusly they use ShowInputSelectionToolNode

(input as file path , problem: it what was be issuse in the image)

**ResponseGeneratorNode** : Used when sufficient information already exists.

(input as latest tool output [-1], previous chat history[-2] ,current query)

---

## Inputs

Rewritten question

Previous message

Conversation history[-2]

Image availability

## Output

```
route:
- ShowInputSelectionToolNode
- ImageValidationToolNode
- ResponseGeneratorNode
```

---

## Pydantic Schema

```python
from enum import Enum

class RouteEnum(str, Enum):
    SHOW_INPUT = "ShowInputSelectionToolNode"
    IMAGE_VALIDATION = "ImageValidationToolNode"
    RESPONSE_GENERATOR = "ResponseGeneratorNode"
class RouterOutput(BaseModel):
    route: RouteEnum
```

---

## Prompt

```
You are an intent router for a food-delivery support workflow.

Question

{question}

Conversation History

{history[-2]}

Available Nodes

1 ShowInputSelectionToolNode
   - Use when the customer needs to upload an image. (OR) Image uploading required
2 ImageValidationToolNode
   - Use when an image path already exists.
3 ResponseGeneratorNode
   - Use when all required information is already available (or) for the conversation needed for answering the question.

Rules
- Never answer the user.
- Only decide the next node.
- Return JSON only.
{
   "route":"ImageValidationToolNode"
}
```

---

# Node 5

# ShowInputSelectionToolNode

## Purpose

Requests image input from the customer. No AI model required.

## Output

```
Please enter file path
```

## Example

```
- Please enter file path
```

---

# Node 6

# ImageValidationToolNode

## Purpose

Runs image validation. , This node combines , EXIF Metadata Extraction ,Vision Language Model ,Validation status

---

## Steps

Load image

↓

Extract EXIF

↓

Check metadata

↓

Run VLM

↓

Return validation

---

# Metadata Tool

Uses

```
PIL.Image

Image.getexif()

PIL.ExifTags.TAGS
```

Returns

```
dict
```

---

# MetadataDecisionComponent

Purpose : Determines whether EXIF metadata exists.

Output : True / False

---

# VLMValidationComponent

Purpose

Input as : str 

Uses OpenRouter Vision model.

Checks

Image relevance

* Input sence present or not using input {str} those realed thing present or not

Output:
    validation=True

---

Suggested model

```
meta-llama/llama-3.2-11b-vision-instruct
```

via OpenRouter.

---

# FakeClaimDetectionComponent

Runs when

Metadata missing

Produces

```
validation=False

reason="Missing metadata"
```

---

## Output Schema

```python
class ImageValidationOutput(BaseModel):

    metadata_found: bool
    validation: bool
    validation_error: bool


```

---

# Node 7

# ShouldContinueToolExecutionNode

## Purpose

Retry failed validation.

---

## Logic

```
if validation_error

and

iteration_count < 3

↓

Retry ImageValidationToolNode

Else

↓

Return ToolRouterNode
```

---

Maximum retries

```
3
```

---

# Node 8

# ResponseGeneratorNode

## Purpose

Produces the final customer response.

This is the only node that communicates with the customer after routing and validation are complete.

---

## Inputs

History

Enhanced question

tool ouputs [-1]

---

## Output

```
final_answer
```

---

## Schema

```python
class ResponseOutput(BaseModel):
    final_answer: str
```

---

## Prompt

```
You are a professional customer-support assistant.

Use

Conversation history

Enhanced question

Image validation results

Metadata

VLM analysis

Generate a helpful response.

Never invent information.

If validation failed explain why.

If image supports the claim mention that.

Return JSON only.

{
    "final_answer":"..."
}
```

---

# LLM Configuration

## Groq

Used for

QuestionRewriterNode

QuestionClassifierNode

ToolRouterNode

ResponseGeneratorNode

Recommended low-cost model

```
llama-3.3-70b-versatile
```

or

```
openai/gpt-oss-20b
```

(whichever is available and meets your latency/cost goals).

---

## OpenRouter

Used only for

VLMValidationComponent

Recommended

```
meta-llama/llama-3.2-11b-vision-instruct
```

---

# Output Validation Strategy

Every LLM node should return structured JSON that is validated with a Pydantic model before the workflow continues.

Recommended flow:

1. Invoke the LLM.
2. Parse the JSON response.
3. Validate it with the corresponding Pydantic model.
4. If validation succeeds, update the workflow state.
5. If validation fails, retry (or route through the retry node if applicable).

This ensures downstream nodes always receive well-typed data and reduces failures caused by malformed model outputs.

---

# Folder Structure

```
project/

├── main.py
│   Entry point and workflow testing.

├── graph.py
│   LangGraph construction, node registration, and routing.

├── schema.py
│   Pydantic models for workflow state and all node outputs.

├── prompts.py
│   Prompt templates for every LLM-powered node.

├── chains.py
│   LangChain chains, Groq/OpenRouter clients, output parsers, and reusable chain builders.

├── tools.py
│   EXIF metadata extraction, image validation helpers, VLM invocation, and utility tools.

├── requirements.txt
│   Project dependencies.

└── Workflow.txt
    Architecture documentation and execution flow.
```

This modular separation keeps prompts, schemas, tools, orchestration, and testing independent, making the project easier to extend, debug, and maintain.

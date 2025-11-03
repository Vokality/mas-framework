# Chemistry Tutoring Example

This example demonstrates two OpenAI-powered agents exchanging information using the MAS framework:

- **Student Agent**: Asks chemistry questions about a specific topic
- **Professor Agent**: Provides detailed, educational answers

## Architecture

The agents communicate peer-to-peer through Redis pub/sub:

1. Professor agent starts and registers with capabilities `["chemistry_professor", "educator"]`
2. Student agent starts and discovers the professor by capability
3. Student uses OpenAI to generate thoughtful chemistry questions
4. Professor uses OpenAI to generate educational explanations
5. The conversation continues for 3 questions, maintaining context

## Prerequisites

1. **Redis**: Running locally on port 6379
   ```bash
   # macOS with Homebrew
   brew install redis
   brew services start redis
   
   # Or with Docker
   docker run -d -p 6379:6379 redis:latest
   ```

2. **OpenAI API Key**: Add to `.env` file in project root or set as environment variable
   ```bash
   # Option 1: Add to .env file (recommended)
   echo "OPENAI_API_KEY=your-api-key-here" >> ../../.env
   
   # Option 2: Set as environment variable
   export OPENAI_API_KEY='your-api-key-here'
   ```

3. **Python Dependencies**: Install the required packages using `uv`
   ```bash
   # Install with uv (project package manager)
   uv pip install openai python-dotenv
   
   # Or the run script will install them automatically
   ./run.sh
   ```

## Running the Example

From the `examples/chemistry_tutoring` directory:

```bash
# Quick start with the run script (recommended)
./run.sh

# Or manually with uv
uv run python main.py

# Or from the project root
uv run python -m examples.chemistry_tutoring
```

The example will automatically load your OpenAI API key from the `.env` file in the project root.

## Example Output

```
2024-01-15 10:30:00 - INFO - Starting Chemistry Tutoring Demo
============================================================
2024-01-15 10:30:00 - INFO - Professor agent professor_chen started
2024-01-15 10:30:00 - INFO - Student agent student_alex started
2024-01-15 10:30:01 - INFO - Found professor: professor_chen

============================================================
STUDENT'S QUESTION #1:
Can you explain what makes ionic bonds different from covalent bonds?
============================================================

2024-01-15 10:30:03 - INFO - Received question from student_alex

============================================================
PROFESSOR'S ANSWER:
Ionic and covalent bonds are two fundamental types of chemical bonds...
[detailed explanation]
============================================================

[... continues for 3 questions ...]
```

## Customization

### Change the Chemistry Topic

Edit `student_agent.py`:

```python
self.current_topic = "organic chemistry"  # or "acid-base reactions", etc.
```

### Adjust Number of Questions

Edit `student_agent.py`:

```python
self.max_questions = 5  # Ask 5 questions instead of 3
```

### Use Different OpenAI Models

When creating agents in `main.py`:

```python
professor = ProfessorAgent(
    model="gpt-4",  # Use GPT-4 instead of gpt-4o-mini
)
```

### Change Agent Personalities

Modify the system prompts in:
- `student_agent.py` - Line ~75 (student personality)
- `professor_agent.py` - Line ~80 (professor teaching style)

## How It Works

### Student Agent

1. **Discovery**: Finds professor agent by searching for `chemistry_professor` capability
2. **Question Generation**: Uses OpenAI with conversation context to generate relevant questions
3. **Message Handling**: Processes answers and asks follow-up questions
4. **Conversation History**: Maintains context across the entire tutoring session

### Professor Agent

1. **Listening**: Waits for question messages from any student
2. **Answer Generation**: Uses OpenAI to create educational, detailed explanations
3. **Response**: Sends answers back to the specific student who asked
4. **Tracking**: Counts questions answered for session statistics

### Message Flow

```
┌──────────┐                           ┌───────────┐
│ Student  │                           │ Professor │
└────┬─────┘                           └─────┬─────┘
     │                                       │
     │  1. Discover("chemistry_professor")   │
     │──────────────────────────────────────>│
     │                                       │
     │  2. Send question (type="question")   │
     │──────────────────────────────────────>│
     │                                       │
     │           3. Generate answer          │
     │                                       │
     │  4. Send answer (type="answer")       │
     │<──────────────────────────────────────│
     │                                       │
     │  5. Generate follow-up question       │
     │                                       │
     │  6. Send next question                │
     │──────────────────────────────────────>│
     │                                       │
```

## Key MAS Framework Features Demonstrated

- **Agent Discovery**: Finding agents by capabilities
- **Peer-to-Peer Messaging**: Direct communication without central routing
- **Message Payloads**: Structured data exchange with types
- **Async Operations**: Non-blocking agent interactions
- **Service Registration**: Automatic agent registry management
- **Conversation State**: Maintaining context across multiple exchanges

## Troubleshooting

### "No professor found"
- Ensure both agents start (professor starts first)
- Check Redis is running: `redis-cli ping` (should return "PONG")

### OpenAI API Errors
- Verify API key is set: `echo $OPENAI_API_KEY`
- Check API quota and billing status
- Ensure you have access to the specified model

### Connection Refused
- Start Redis: `brew services start redis` or `docker run -d -p 6379:6379 redis`
- Check Redis port: `redis-cli -p 6379 ping`

## Next Steps

- Add more student agents creating a classroom scenario
- Implement different subjects (physics, math, biology)
- Add conversation summarization after the session
- Store Q&A history in agent state for review
- Add a moderator agent to manage multiple tutoring sessions

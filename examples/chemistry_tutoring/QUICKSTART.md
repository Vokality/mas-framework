# Quick Start Guide

## 5-Minute Setup

### 1. Install Dependencies

This project uses `uv` as the package manager.

```bash
# Install with uv
uv pip install openai python-dotenv

# Or let the run script handle it
# (run.sh will automatically install dependencies)
```

### 2. Start Redis

**macOS with Homebrew:**
```bash
brew install redis
brew services start redis
```

**Docker:**
```bash
docker run -d -p 6379:6379 redis:latest
```

**Linux:**
```bash
sudo apt-get install redis-server
sudo systemctl start redis
```

### 3. Set OpenAI API Key

**Option 1: Add to .env file (Recommended)**
```bash
# Create/edit .env file in project root
cd /path/to/mas-framework
echo "OPENAI_API_KEY=sk-..." >> .env
```

**Option 2: Export as environment variable**
```bash
export OPENAI_API_KEY='sk-...'
```

The example will automatically load the key from the `.env` file in the project root.

### 4. Run the Demo

```bash
# Easy way - use the run script (recommended)
./run.sh

# Manual way with uv
uv run python main.py

# Or from project root
uv run python -m examples.chemistry_tutoring
```

## What to Expect

The demo will:

1. Start the MAS service (handles agent registration)
2. Start the Professor agent (registers with capability `chemistry_professor`)
3. Start the Student agent (discovers the professor)
4. Student asks 3 chemistry questions about chemical bonding
5. Professor provides detailed educational answers
6. Conversation maintains context across all exchanges

**Expected runtime:** ~30-60 seconds for 3 question/answer pairs

## Sample Output

```
2024-11-03 11:35:00 - INFO - Starting Chemistry Tutoring Demo
============================================================
2024-11-03 11:35:00 - INFO - MAS Service started
2024-11-03 11:35:01 - INFO - Professor agent professor_chen started
2024-11-03 11:35:01 - INFO - Student agent student_alex started
2024-11-03 11:35:02 - INFO - Found professor: professor_chen

============================================================
STUDENT'S QUESTION #1:
I'm having trouble understanding the difference between ionic and 
covalent bonds. Could you explain what makes them different and 
maybe give me some examples?
============================================================

2024-11-03 11:35:04 - INFO - Received question from student_alex

============================================================
PROFESSOR'S ANSWER:
Great question! The key difference between ionic and covalent bonds 
lies in how electrons are shared between atoms.

In ionic bonding, electrons are transferred from one atom to another, 
creating charged ions. For example, when sodium (Na) bonds with 
chlorine (Cl) to form table salt (NaCl), sodium gives up one electron 
to become Na+, while chlorine gains that electron to become Cl-. 
These opposite charges attract, creating the ionic bond.

In covalent bonding, atoms share electrons rather than transferring 
them completely. Think of water (H2O): oxygen shares electrons with 
two hydrogen atoms. Neither atom gives up or gains electrons completely; 
they share them like partners.

A helpful way to remember: ionic bonds typically form between metals 
and nonmetals, while covalent bonds form between nonmetals. The 
difference comes down to electronegativity - how strongly atoms 
attract electrons!
============================================================

[... continues for questions 2 and 3 ...]

2024-11-03 11:35:45 - INFO - Tutoring session complete!
============================================================
Student says: Thank you for helping me understand chemistry!
Total questions answered: 3
============================================================

2024-11-03 11:35:45 - INFO - Demo complete!
```

## Architecture Diagram

```
┌─────────────────┐         ┌──────────────────┐
│  Student Agent  │         │ Professor Agent  │
│  (student_alex) │         │ (professor_chen) │
└────────┬────────┘         └────────┬─────────┘
         │                           │
         │  1. discover("chemistry_professor")
         │──────────────────────────>│
         │                           │
         │  2. "What is ionic bonding?"
         │──────────────────────────>│
         │                           │
         │    OpenAI generates       │
         │    chemistry question     │  OpenAI generates
         │                           │  educational answer
         │                           │
         │  3. "Ionic bonds form..." │
         │<──────────────────────────│
         │                           │
         │  4. Generate follow-up    │
         │                           │
         │  5. Next question         │
         │──────────────────────────>│
         │                           │
         └───────────┬───────────────┘
                     │
              ┌──────▼──────┐
              │    Redis    │
              │  (pub/sub)  │
              └─────────────┘
```

## Customization Ideas

1. **Change the topic**: Edit `student_agent.py`, line ~44
   ```python
   self.current_topic = "organic chemistry"
   ```

2. **More questions**: Edit `student_agent.py`, line ~43
   ```python
   self.max_questions = 5
   ```

3. **Different model**: Edit `main.py`, lines ~35-40
   ```python
   professor = ProfessorAgent(
       model="gpt-4",  # Use GPT-4 instead
   )
   ```

4. **Multiple students**: Create multiple student agents in `main.py`
   ```python
   student1 = StudentAgent("alice")
   student2 = StudentAgent("bob")
   ```

## Troubleshooting

**"Redis is not running"**
- Run `redis-cli ping` to test connection
- Start Redis with commands shown in step 2

**"OPENAI_API_KEY not set"**
- Export the key: `export OPENAI_API_KEY='your-key'`
- Verify with: `echo $OPENAI_API_KEY`

**"Import openai could not be resolved"**
- Install OpenAI: `uv pip install openai python-dotenv`
- Or run: `./run.sh` (auto-installs dependencies)

**No questions being asked**
- Check both agents started successfully
- Verify Redis connection
- Check logs for error messages

## Next Steps

After running this example, try:

1. Reading the [full README](README.md) for detailed documentation
2. Modifying agent personalities in the system prompts
3. Adding a third "teaching assistant" agent
4. Implementing conversation history export
5. Creating agents for different subjects (math, physics, etc.)

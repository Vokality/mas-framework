# Example Output

This shows what a typical run of the chemistry tutoring demo looks like.

## Full Console Output

```
================================
Chemistry Tutoring Demo
================================

Checking Redis connection...
✓ Redis is running

✓ OpenAI API key is set

Checking Python dependencies...
✓ Dependencies installed

Starting demo...
================================

2024-11-03 11:35:00,123 - mas.service - INFO - MAS Service starting
2024-11-03 11:35:00,125 - mas.service - INFO - MAS Service started
2024-11-03 11:35:00,234 - mas.agent - INFO - Agent started
2024-11-03 11:35:00,234 - professor_agent - INFO - Professor agent professor_chen started, ready to answer questions...
2024-11-03 11:35:00,345 - mas.agent - INFO - Agent started
2024-11-03 11:35:00,345 - student_agent - INFO - Student agent student_alex started, looking for professor...
2024-11-03 11:35:00,850 - student_agent - INFO - Found professor: professor_chen

============================================================
STUDENT'S QUESTION #1:
I'm confused about ionic and covalent bonds. What's the main 
difference between them? How do I know which type of bond will 
form between two atoms?
============================================================

2024-11-03 11:35:03,456 - professor_agent - INFO - Received question from student_alex

============================================================
PROFESSOR'S ANSWER:
Great question! The fundamental difference between ionic and covalent 
bonds comes down to how electrons behave when atoms interact.

In ionic bonding, one atom essentially gives up one or more electrons 
to another atom. This creates two ions with opposite charges - one 
positive (lost electrons) and one negative (gained electrons). These 
opposite charges attract each other strongly. A classic example is 
sodium chloride (table salt): sodium loses an electron to become Na+, 
and chlorine gains that electron to become Cl-. The attraction between 
Na+ and Cl- is the ionic bond.

In covalent bonding, atoms share electrons rather than transferring 
them. Think of it as a partnership where both atoms contribute electrons 
to form a shared electron cloud between them. Water (H2O) is a perfect 
example: oxygen shares its electrons with hydrogen atoms, and they all 
benefit from the arrangement.

The key to predicting which type forms is electronegativity - how 
strongly an atom attracts electrons. Large differences in 
electronegativity (typically >1.7) lead to ionic bonds, while smaller 
differences lead to covalent bonds. Generally, metals bonding with 
nonmetals form ionic bonds, while nonmetals bonding together form 
covalent bonds.
============================================================

2024-11-03 11:35:08,567 - student_agent - INFO - 
============================================================
STUDENT'S QUESTION #2:
That makes sense! But I'm still not sure about polar covalent bonds. 
How are they different from regular covalent bonds? And what does 
polarity actually mean?
============================================================

2024-11-03 11:35:11,678 - professor_agent - INFO - Received question from student_alex

============================================================
PROFESSOR'S ANSWER:
Excellent follow-up question! You're really digging into the nuances here.

A polar covalent bond is like a middle ground between a pure covalent 
bond and an ionic bond. In a pure (nonpolar) covalent bond, electrons 
are shared equally between atoms. Think of two identical atoms like H2 
or O2 - they have equal "pulling power" on the shared electrons.

In a polar covalent bond, the electrons are still shared, but not 
equally. One atom pulls the shared electrons closer to itself because 
it's more electronegative. This creates a slight negative charge (δ-) 
on the atom that pulls harder, and a slight positive charge (δ+) on 
the other atom. The word "polar" means having opposite charges at 
different ends, like a magnet has north and south poles.

Water is the perfect example. In an H-O-H molecule, oxygen is much 
more electronegative than hydrogen, so it pulls the shared electrons 
closer. This makes the oxygen end slightly negative and the hydrogen 
ends slightly positive. This polarity is why water has so many unique 
properties - those slightly charged ends attract other water molecules 
and make water an excellent solvent.

The key difference: nonpolar covalent bonds have equal sharing (like 
splitting something 50/50), polar covalent bonds have unequal sharing 
(like 70/30), and ionic bonds are complete transfer (100/0).
============================================================

2024-11-03 11:35:16,789 - student_agent - INFO - 
============================================================
STUDENT'S QUESTION #3:
This is really helpful! One last thing - can you explain why knowing 
about bond polarity is important? Does it affect how molecules behave?
============================================================

2024-11-03 11:35:19,890 - professor_agent - INFO - Received question from student_alex

============================================================
PROFESSOR'S ANSWER:
I'm so glad you asked this! Understanding polarity is crucial because 
it affects almost everything about how molecules behave and interact.

First, polarity determines solubility - the classic "like dissolves 
like" rule. Polar molecules dissolve well in polar solvents (like salt 
in water), and nonpolar molecules dissolve well in nonpolar solvents 
(like oil in gasoline). This is why oil and water don't mix - water is 
polar, oil is nonpolar, and they can't interact effectively.

Polarity also affects boiling and melting points. Polar molecules 
stick to each other more strongly than nonpolar molecules of similar 
size, so they need more energy to separate. This is why water (polar) 
has a much higher boiling point than methane (nonpolar), even though 
they have similar molecular weights.

In biology, polarity is absolutely essential. Cell membranes work 
because they have both polar and nonpolar parts, allowing them to 
control what enters and exits cells. Drug design depends on polarity 
- drugs need the right balance of polar and nonpolar regions to be 
absorbed by the body and reach their targets.

Even everyday phenomena like why wet things stick together, how soap 
cleans, and why some materials are good insulators while others conduct 
electricity - all come down to molecular polarity! Once you understand 
this concept, you'll see it everywhere in chemistry and biology.
============================================================

2024-11-03 11:35:20,123 - student_agent - INFO - Tutoring session complete! Thank you, professor.

============================================================
Student says: Thank you for helping me understand chemistry!
Total questions answered: 3
============================================================

2024-11-03 11:35:21,234 - mas.agent - INFO - Agent stopped
2024-11-03 11:35:21,235 - mas.agent - INFO - Agent stopped
2024-11-03 11:35:21,236 - mas.service - INFO - MAS Service stopped
Demo complete!
```

## Key Observations

### Conversation Flow
1. **Discovery**: Student finds professor by capability lookup
2. **Context Building**: Each question builds on previous answers
3. **Natural Progression**: From basic concepts to applications
4. **Maintained State**: Conversation history tracked throughout

### Agent Behaviors

**Student Agent:**
- Generates contextually relevant questions
- Maintains conversation history
- Shows curiosity through follow-up questions
- Politely concludes the session

**Professor Agent:**
- Provides detailed, educational explanations
- Uses analogies and examples
- Builds on previous concepts
- Encourages further understanding

### Technical Features Demonstrated

1. **Service Discovery**: Finding agents by capability
2. **Peer-to-Peer Messaging**: Direct agent-to-agent communication
3. **Async Operations**: Non-blocking message handling
4. **Structured Payloads**: Type-based message routing
5. **State Management**: Tracking conversation progress

### OpenAI Integration

- Student uses GPT to generate thoughtful questions
- Professor uses GPT to create educational responses
- Both maintain their roles consistently
- Conversation builds naturally through context

## Timing

Typical execution times:
- **Startup**: 1-2 seconds (service + agent initialization)
- **Per Q&A**: 5-10 seconds (OpenAI API calls)
- **Total Runtime**: 30-60 seconds for 3 exchanges
- **Cleanup**: <1 second

## Message Count

For a 3-question session:
- **Redis messages**: ~10 (registration, discovery, 3 Q&A pairs)
- **OpenAI API calls**: 6 (3 questions + 3 answers)
- **System events**: 4 (register × 2, deregister × 2)

## Resource Usage

- **Memory**: ~50MB per agent
- **Redis keys**: 6-8 (agent metadata, state, heartbeats)
- **Network**: Minimal (local Redis pub/sub)
- **CPU**: Mostly idle (waiting on OpenAI API)

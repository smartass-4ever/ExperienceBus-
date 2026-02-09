"""
Experience Bus: Collective Learning for Small Agent Teams

Solves two critical problems in small agentic systems:
1. "Groundhog Day" loops - agents repeating the same mistakes
2. Race conditions - concurrent access to shared state

Designed for teams of 5-10 agents where collective intelligence matters
more than raw scale.

Origin: RFC for Aden Hive #3761
"""

import asyncio
import time
import json
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from collections import defaultdict


class SignalType(Enum):
    """Types of experience signals agents can broadcast"""
    ERROR_RESOLVED = "error_resolved"        # Agent fixed a bug
    WORKAROUND_FOUND = "workaround_found"    # Agent found API workaround
    OPTIMIZATION = "optimization"            # Agent discovered faster approach
    VALIDATION_PASSED = "validation_passed"  # Agent validated a solution
    VALIDATION_FAILED = "validation_failed"  # Agent found invalid approach
    CONTEXT_UPDATE = "context_update"        # New information discovered


@dataclass
class ExperienceSignal:
    """
    Heuristic signal broadcast by an agent to share learning.
    
    Instead of agents writing to shared memory (causing race conditions),
    they broadcast signals to the orchestrator who then "immunizes" the swarm.
    """
    signal_id: str
    agent_id: str
    signal_type: SignalType
    timestamp: float
    
    # The actual learning/discovery
    discovery: Dict[str, Any]  # What was learned
    context: Dict[str, Any]    # When/where it applies
    mitigation: Optional[str] = None  # How to avoid the issue
    
    # Propagation tracking
    propagated_to: Set[str] = field(default_factory=set)
    
    def to_dict(self) -> Dict:
        data = asdict(self)
        data['signal_type'] = self.signal_type.value
        data['propagated_to'] = list(self.propagated_to)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'ExperienceSignal':
        data['signal_type'] = SignalType(data['signal_type'])
        data['propagated_to'] = set(data['propagated_to'])
        return cls(**data)


class ExperienceBus:
    """
    Active event bus that orchestrates collective learning.
    
    Key difference from passive EventBus:
    - Passive: Agents write to shared memory (race conditions)
    - Active: Agents broadcast to bus, orchestrator serializes updates
    
    This ensures:
    - No race conditions (single writer: orchestrator)
    - Collective immunity (all agents learn from one agent's discovery)
    - No agent-to-agent communication (security/isolation maintained)
    """
    
    def __init__(self):
        # Signal storage
        self.signals: Dict[str, ExperienceSignal] = {}
        self.signal_queue: asyncio.Queue = asyncio.Queue()
        
        # Agent tracking
        self.active_agents: Set[str] = set()
        self.agent_contexts: Dict[str, Dict[str, Any]] = {}
        
        # Learning index (for fast lookup)
        self.error_solutions: Dict[str, List[str]] = defaultdict(list)
        self.workarounds: Dict[str, List[str]] = defaultdict(list)
        self.validations: Dict[str, bool] = {}
        
        # Statistics
        self.broadcasts_sent = 0
        self.immunizations = 0  # How many times we prevented repeat mistakes
        
        # Synchronization
        self._lock = asyncio.Lock()
        self._running = False
        
    async def start(self):
        """Start the experience bus processing loop"""
        self._running = True
        asyncio.create_task(self._process_signals())
    
    async def stop(self):
        """Stop the experience bus"""
        self._running = False
    
    async def register_agent(self, agent_id: str, initial_context: Dict[str, Any]):
        """
        Register an agent with the swarm.
        
        Agent receives all relevant historical learnings immediately.
        """
        async with self._lock:
            self.active_agents.add(agent_id)
            self.agent_contexts[agent_id] = initial_context
            
            # Immediately share relevant historical learnings
            await self._bootstrap_agent(agent_id)
    
    async def unregister_agent(self, agent_id: str):
        """Remove agent from active swarm"""
        async with self._lock:
            self.active_agents.discard(agent_id)
            if agent_id in self.agent_contexts:
                del self.agent_contexts[agent_id]
    
    async def broadcast_signal(self, signal: ExperienceSignal):
        """
        Agent broadcasts an experience signal.
        
        This is non-blocking - agent continues work while
        orchestrator handles propagation.
        """
        await self.signal_queue.put(signal)
        self.broadcasts_sent += 1
    
    async def _process_signals(self):
        """
        Main orchestrator loop.
        
        Processes signals sequentially (no race conditions).
        Propagates learnings to relevant agents.
        """
        while self._running:
            try:
                # Wait for next signal (with timeout to allow shutdown)
                signal = await asyncio.wait_for(
                    self.signal_queue.get(),
                    timeout=1.0
                )
                
                # Process signal under lock (serialized access)
                async with self._lock:
                    await self._handle_signal(signal)
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Error processing signal: {e}")
    
    async def _handle_signal(self, signal: ExperienceSignal):
        """
        Handle a single experience signal.
        
        Steps:
        1. Store the learning
        2. Index it for fast lookup
        3. Identify which agents need this knowledge
        4. "Immunize" those agents
        """
        # Store signal
        self.signals[signal.signal_id] = signal
        
        # Index based on type
        if signal.signal_type == SignalType.ERROR_RESOLVED:
            error_key = signal.discovery.get('error_type', 'unknown')
            self.error_solutions[error_key].append(signal.signal_id)
            
        elif signal.signal_type == SignalType.WORKAROUND_FOUND:
            api = signal.discovery.get('api', 'unknown')
            self.workarounds[api].append(signal.signal_id)
            
        elif signal.signal_type in [SignalType.VALIDATION_PASSED, SignalType.VALIDATION_FAILED]:
            approach = signal.discovery.get('approach')
            self.validations[approach] = (signal.signal_type == SignalType.VALIDATION_PASSED)
        
        # Propagate to relevant agents
        await self._propagate_to_swarm(signal)
    
    async def _propagate_to_swarm(self, signal: ExperienceSignal):
        """
        "Immunize" the swarm by pushing learning to all relevant agents.
        
        This is the key difference: instead of agents fighting for shared state,
        the orchestrator actively pushes updates.
        """
        propagated_count = 0
        
        for agent_id in self.active_agents:
            # Don't send back to originating agent
            if agent_id == signal.agent_id:
                continue
            
            # Check if this learning is relevant to this agent
            if self._is_relevant(signal, agent_id):
                # Update agent's context with the learning
                await self._update_agent_context(agent_id, signal)
                signal.propagated_to.add(agent_id)
                propagated_count += 1
        
        if propagated_count > 0:
            self.immunizations += propagated_count
            print(f"✓ Immunized {propagated_count} agents with {signal.signal_type.value}")
    
    def _is_relevant(self, signal: ExperienceSignal, agent_id: str) -> bool:
        """
        Determine if a signal is relevant to a specific agent.
        
        Relevance rules:
        - Error solutions: Always relevant (prevent future errors)
        - Workarounds: Relevant if agent uses that API
        - Validations: Relevant if agent might try that approach
        """
        agent_context = self.agent_contexts.get(agent_id, {})
        
        if signal.signal_type == SignalType.ERROR_RESOLVED:
            # Error solutions are always relevant (prevention)
            return True
        
        if signal.signal_type == SignalType.WORKAROUND_FOUND:
            # Relevant if agent uses this API
            api = signal.discovery.get('api')
            agent_apis = agent_context.get('apis_used', [])
            return api in agent_apis
        
        if signal.signal_type in [SignalType.VALIDATION_PASSED, SignalType.VALIDATION_FAILED]:
            # Relevant if agent working on similar problem
            problem_domain = signal.context.get('domain')
            agent_domain = agent_context.get('domain')
            return problem_domain == agent_domain
        
        # Context updates are always relevant
        return True
    
    async def _update_agent_context(self, agent_id: str, signal: ExperienceSignal):
        """
        Update an agent's context with new learning.
        
        In practice, this would push the update to the agent's
        execution context or message queue.
        """
        if agent_id not in self.agent_contexts:
            return
        
        # Add to agent's known solutions
        if 'learned_solutions' not in self.agent_contexts[agent_id]:
            self.agent_contexts[agent_id]['learned_solutions'] = []
        
        self.agent_contexts[agent_id]['learned_solutions'].append({
            'signal_id': signal.signal_id,
            'type': signal.signal_type.value,
            'discovery': signal.discovery,
            'learned_at': time.time()
        })
    
    async def _bootstrap_agent(self, agent_id: str):
        """
        When a new agent joins, give it all relevant historical learnings.
        
        This prevents new agents from repeating old mistakes.
        """
        agent_context = self.agent_contexts.get(agent_id, {})
        
        # Share all error solutions
        for error_type, signal_ids in self.error_solutions.items():
            for signal_id in signal_ids:
                signal = self.signals.get(signal_id)
                if signal:
                    await self._update_agent_context(agent_id, signal)
        
        print(f"✓ Bootstrapped {agent_id} with {len(self.signals)} historical learnings")
    
    async def query_experience(self, 
                              query_type: SignalType,
                              context: Dict[str, Any]) -> List[ExperienceSignal]:
        """
        Query the experience bus for relevant learnings.
        
        Agents can proactively check for solutions before attempting work.
        """
        async with self._lock:
            results = []
            
            if query_type == SignalType.ERROR_RESOLVED:
                error_type = context.get('error_type')
                signal_ids = self.error_solutions.get(error_type, [])
                results = [self.signals[sid] for sid in signal_ids if sid in self.signals]
            
            elif query_type == SignalType.WORKAROUND_FOUND:
                api = context.get('api')
                signal_ids = self.workarounds.get(api, [])
                results = [self.signals[sid] for sid in signal_ids if sid in self.signals]
            
            return results
    
    def get_stats(self) -> Dict[str, Any]:
        """Get experience bus statistics"""
        return {
            'total_signals': len(self.signals),
            'active_agents': len(self.active_agents),
            'broadcasts_sent': self.broadcasts_sent,
            'immunizations': self.immunizations,
            'error_solutions_learned': len(self.error_solutions),
            'workarounds_discovered': len(self.workarounds),
            'validations_recorded': len(self.validations),
            'avg_propagation': self.immunizations / max(self.broadcasts_sent, 1)
        }


class Agent:
    """
    Example agent that uses the Experience Bus.
    
    Key behaviors:
    - Broadcasts discoveries to the bus
    - Receives immunizations from orchestrator
    - No direct agent-to-agent communication
    """
    
    def __init__(self, agent_id: str, specialty: str, bus: ExperienceBus):
        self.agent_id = agent_id
        self.specialty = specialty
        self.bus = bus
        self.learned_solutions: List[Dict] = []
        
    async def start(self):
        """Register with the experience bus"""
        await self.bus.register_agent(
            self.agent_id,
            {
                'specialty': self.specialty,
                'apis_used': ['openai', 'anthropic'],
                'domain': 'code_generation'
            }
        )
    
    async def stop(self):
        """Unregister from the bus"""
        await self.bus.unregister_agent(self.agent_id)
    
    async def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a task with experience-aware error handling.
        
        Flow:
        1. Check for known solutions first
        2. Attempt task
        3. If error occurs, check if someone else solved it
        4. If you solve it, broadcast to immunize others
        """
        # Step 1: Check for known solutions
        known_solution = await self._check_known_solutions(task)
        if known_solution:
            print(f"  {self.agent_id}: Using known solution (no trial-and-error needed)")
            return {"status": "success", "method": "learned_solution"}
        
        # Step 2: Attempt task
        result = await self._attempt_task(task)
        
        # Step 3: If error, check if someone solved it
        if result.get("status") == "error":
            error_type = result.get("error_type")
            
            # Query bus for solutions
            solutions = await self.bus.query_experience(
                SignalType.ERROR_RESOLVED,
                {"error_type": error_type}
            )
            
            if solutions:
                print(f"  {self.agent_id}: Found solution from another agent!")
                solution = solutions[0]
                return await self._apply_solution(solution)
            
            # Step 4: Solve it ourselves and broadcast
            print(f"  {self.agent_id}: No known solution, solving from scratch...")
            fixed_result = await self._solve_error(error_type)
            
            if fixed_result.get("status") == "success":
                # Broadcast solution to immunize others
                await self._broadcast_solution(error_type, fixed_result["solution"])
            
            return fixed_result
        
        return result
    
    async def _check_known_solutions(self, task: Dict) -> Optional[Dict]:
        """Check if we already know how to do this"""
        # In practice, check self.learned_solutions
        # For demo, we'll just return None
        return None
    
    async def _attempt_task(self, task: Dict) -> Dict:
        """Simulate attempting a task (may fail)"""
        await asyncio.sleep(0.1)
        
        # Simulate random failures
        import random
        if random.random() < 0.3:  # 30% failure rate
            return {
                "status": "error",
                "error_type": "api_timeout",
                "message": "API request timed out"
            }
        
        return {"status": "success"}
    
    async def _solve_error(self, error_type: str) -> Dict:
        """Solve an error through trial and error"""
        await asyncio.sleep(0.2)  # Simulate problem-solving time
        
        return {
            "status": "success",
            "solution": {
                "error_type": error_type,
                "mitigation": "Retry with exponential backoff",
                "code": "await retry_with_backoff(api_call)"
            }
        }
    
    async def _apply_solution(self, solution: ExperienceSignal) -> Dict:
        """Apply a solution learned from another agent"""
        await asyncio.sleep(0.05)  # Much faster than solving from scratch
        return {"status": "success", "method": "applied_learned_solution"}
    
    async def _broadcast_solution(self, error_type: str, solution: Dict):
        """Broadcast solution to immunize other agents"""
        signal = ExperienceSignal(
            signal_id=f"{self.agent_id}_{time.time()}",
            agent_id=self.agent_id,
            signal_type=SignalType.ERROR_RESOLVED,
            timestamp=time.time(),
            discovery={
                "error_type": error_type,
                "solution": solution
            },
            context={"agent": self.agent_id},
            mitigation=solution.get("mitigation")
        )
        
        await self.bus.broadcast_signal(signal)
        print(f"  {self.agent_id}: Broadcasted solution to swarm")

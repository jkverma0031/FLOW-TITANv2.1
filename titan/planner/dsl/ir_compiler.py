# Path: titan/planner/dsl/ir_compiler.py
from __future__ import annotations
from typing import List, Dict, Any, Optional, Tuple
import uuid
import logging

from titan.schemas.graph import (
    CFGNodeType, CFGNode, TaskNode, DecisionNode, LoopNode, RetryNode, NoOpNode, StartNode, EndNode
)
from titan.planner.dsl.ir_dsl import (
    ASTRoot, ASTAssign, ASTTaskCall, ASTIf, ASTFor, ASTRetry, ASTExpr, ASTValue
)

logger = logging.getLogger(__name__)

class Compiler:
    """
    Compiler component responsible for translating the validated Abstract Syntax Tree (AST) 
    into a Control Flow Graph (CFG) structure (a list of node dictionaries).
    
    This process fixes the Planner Gap by ensuring the output structure is always valid.
    """
    
    def __init__(self):
        self.node_id_counter = 0
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.entry_node_id: Optional[str] = None
        self.exit_node_id: Optional[str] = None
        self.current_scope_vars: Dict[str, str] = {}
        self.temp_vars: List[str] = []

    def _generate_node_id(self, prefix: str = 'n') -> str:
        """Generates a unique, short node ID."""
        return f"{prefix}{uuid.uuid4().hex[:8]}"
        
    def _create_node(self, node_type: CFGNodeType, name: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        """Helper to create a standard node dictionary compatible with Pydantic schemas."""
        node_id = self._generate_node_id(node_type.value[0])
        node_dict = {
            "id": node_id,
            "type": node_type.value,
            "name": name,
            "successors": {}, 
            "metadata": {}, # Initialize metadata dictionary
            "description": name or node_type.value,
            **kwargs
        }
        self.nodes[node_id] = node_dict
        return node_dict

    def _extract_raw_value(self, node: Any) -> str:
        """
        Unwraps AST objects and defensively strips surrounding quotes.
        """
        raw_text = ""
        if isinstance(node, ASTExpr):
            raw_text = node.text
        elif isinstance(node, ASTValue):
            raw_text = str(node.value)
        else:
            raw_text = str(node)

        # Defensive strip: Remove quotes if they exist (', ", `, etc.)
        if raw_text and (raw_text.startswith('"') and raw_text.endswith('"') or
                         raw_text.startswith("'") and raw_text.endswith("'")):
            return raw_text[1:-1]
        
        return raw_text


    def _parse_expression(self, expr_node: ASTExpr) -> str:
        """
        Converts an AST expression node into a raw string suitable for the ConditionEvaluator.
        """
        return self._extract_raw_value(expr_node)


    def _compile_statement(self, stmt: Any, successor_label: str = 'next') -> Tuple[str, str]:
        """
        Compiles a single AST statement (Assignment or TaskCall).
        Returns (node_id, next_successor_id).
        """
        if isinstance(stmt, ASTAssign):
            task_call = stmt.value
            if not isinstance(task_call, ASTTaskCall):
                raise ValueError(f"Assignment value must be a task call, found {type(task_call)}")
            
            # --- Compile Task Details ---
            task_name_arg_ast = task_call.args.get('name')
            task_name_raw = self._extract_raw_value(task_name_arg_ast)
            
            # FIX: Ensure task_args are correctly mapped to simple strings for compilation
            compiled_args = {
                key: self._extract_raw_value(value)
                for key, value in task_call.args.items() if key != 'name' # Exclude name from args metadata
            }

            # Create Task Node
            task_node_dict = self._create_node(
                CFGNodeType.TASK, 
                name=task_name_raw,
                description=f"Executes task: {task_name_raw}",
                task_ref=task_name_raw, 
                # FIX: Arguments are stored in the metadata dict as required by TaskNode structure
                metadata={'task_args': compiled_args} 
            )
            # Register the assignment variable name for context resolution
            self.current_scope_vars[stmt.target] = task_node_dict['id']
            
            # FIX: Set a dedicated name on the node state for easy lookup by StateTracker
            task_node_dict['metadata']['state_name'] = task_name_raw
            
            return task_node_dict['id'], successor_label

        elif isinstance(stmt, ASTTaskCall):
            # Non-assigned (fire-and-forget) task call
            task_name_arg_ast = stmt.args.get('name')
            task_name_raw = self._extract_raw_value(task_name_arg_ast)
            
            compiled_args = {
                key: self._extract_raw_value(value)
                for key, value in stmt.args.items() if key != 'name'
            }

            task_node_dict = self._create_node(
                CFGNodeType.TASK, 
                name=task_name_raw,
                description=f"Executes task: {task_name_raw} (unassigned)",
                task_ref=task_name_raw,
                metadata={'task_args': compiled_args}
            )
            return task_node_dict['id'], successor_label
            
        elif isinstance(stmt, (ASTIf, ASTFor, ASTRetry)):
            raise NotImplementedError(f"Compound statement compiler logic not implemented for {type(stmt).__name__}.")
        
        raise TypeError(f"Cannot compile unknown AST node type: {type(stmt)}")


    def _compile_block(self, statements: List[Any], next_default_node_id: str) -> str:
        """
        Compiles a sequential list of statements (a block).
        Returns the ID of the first node in the block.
        """
        if not statements:
            return next_default_node_id

        first_node_id = None
        last_node_id = None

        for stmt in statements:
            if isinstance(stmt, (ASTAssign, ASTTaskCall)):
                curr_node_id, _ = self._compile_statement(stmt)
                
                if first_node_id is None:
                    first_node_id = curr_node_id
                
                if last_node_id is not None:
                    self.nodes[last_node_id]['successors']['next'] = curr_node_id
                
                last_node_id = curr_node_id
            
            else:
                 logger.error(f"Skipping unimplemented compound statement type: {type(stmt).__name__}")
                 continue


        if last_node_id is not None:
            self.nodes[last_node_id]['successors']['next'] = next_default_node_id
            logger.debug(f"Block end linked {last_node_id} -> {next_default_node_id}")


        return first_node_id or next_default_node_id


    def compile(self, ast_root: ASTRoot) -> List[Dict[str, Any]]:
        """
        Main entry point for compilation.
        Returns a list of node dictionaries representing the CFG.
        """
        self.nodes = {}
        self.current_scope_vars = {}
        self.node_id_counter = 0

        # 1. Create START and END nodes
        start_node = self._create_node(CFGNodeType.START, name="Start")
        end_node = self._create_node(CFGNodeType.END, name="End")
        self.entry_node_id = start_node['id']
        self.exit_node_id = end_node['id']

        # 2. Compile the main program block
        first_executable_node_id = self._compile_block(ast_root.statements, self.exit_node_id)

        # 3. Link the START node to the first executable statement
        start_node['successors']['next'] = first_executable_node_id

        logger.info(f"COMPILER: CFG created with {len(self.nodes)} nodes.")
        return list(self.nodes.values())
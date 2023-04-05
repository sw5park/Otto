import openai
from typing import Dict, List, Any
from task_manager import TaskManager
import pinecone
from collections import deque
from utils import get_ada_embedding


class BaseAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config

    def __call__(self, **kwargs) -> str:
        prompt = self.config["prompt"].format(**kwargs)
        response = openai.Completion.create(
            engine="text-davinci-003",
            prompt=prompt,
            temperature=self.config.get("temperature", 0.5),
            max_tokens=self.config.get("max_tokens", 1000),
            top_p=self.config.get("top_p", 1),
            frequency_penalty=self.config.get("frequency_penalty", 0),
            presence_penalty=self.config.get("presence_penalty", 0)
        )
        return response.choices[0].text.strip()


class TaskCreationAgent(BaseAgent):
    def create_tasks(self, objective: str, result: Dict, task_description: str, task_manager: TaskManager) -> List[Dict]:
        """
        Create tasks using the task_creation agent.

        :param objective: The objective for the task_creation agent.
        :param result: A dictionary containing the result of the last completed task.
        :param task_description: The task description for the last completed task.
        :param task_manager: An instance of the TaskManager class.
        :return: A list of new tasks generated by the agent.
        """
        task_list = self._get_task_list(task_manager)
        response_text = super().__call__(objective=objective, result=result, task_description=task_description, task_list=', '.join(task_list))
        
        return self._parse_response(response_text)

    @staticmethod
    def _get_task_list(task_manager: TaskManager) -> List[str]:
        """
        Get task list from the task manager.

        :param task_manager: An instance of the TaskManager class.
        :return: List of task names.
        """
        return [t["task_name"] for t in task_manager.task_list]

    @staticmethod
    def _parse_response(response_text: str) -> List[Dict]:
        """
        Parse the response text from the task_creation agent.

        :param response_text: The response text returned by the task_creation agent.
        :return: A list of dictionaries containing task_name.
        """
        new_tasks = response_text.split('\n')
        return [{"task_name": task_name} for task_name in new_tasks]



class PrioritizationAgent(BaseAgent):
    def prioritize_tasks(self, this_task_id: int, objective: str, task_manager: TaskManager) -> None:
        """
        Prioritize tasks using the prioritization agent.

        :param this_task_id: The current task ID.
        :param objective: The objective for the prioritization agent.
        :param task_manager: An instance of the TaskManager class.
        :return: None
        """
        task_names = self._get_task_names(task_manager)
        next_task_id = this_task_id + 1
        response_text = super().__call__(task_names=task_names, objective=objective, next_task_id=next_task_id)
        
        new_tasks = self._parse_response(response_text)
        self._update_task_manager(task_manager, new_tasks)

    @staticmethod
    def _get_task_names(task_manager: TaskManager) -> List[str]:
        """
        Get task names from the task manager.

        :param task_manager: An instance of the TaskManager class.
        :return: List of task names.
        """
        return [t["task_name"] for t in task_manager.task_list]

    @staticmethod
    def _parse_response(response_text: str) -> List[Dict[str, str]]:
        """
        Parse the response text from the prioritization agent.

        :param response_text: The response text returned by the prioritization agent.
        :return: List of dictionaries containing task_id and task_name.
        """
        task_strings = response_text.split('\n')
        tasks = []
        for task_string in task_strings:
            task_parts = task_string.strip().split(".", 1)
            if len(task_parts) == 2:
                task_id = task_parts[0].strip()
                task_name = task_parts[1].strip()
                tasks.append({"task_id": task_id, "task_name": task_name})
        return tasks

    @staticmethod
    def _update_task_manager(task_manager: TaskManager, new_tasks: List[Dict[str, str]]) -> None:
        """
        Update the task manager with the new tasks.

        :param task_manager: An instance of the TaskManager class.
        :param new_tasks: List of dictionaries containing task_id and task_name.
        :return: None
        """
        task_manager.task_list = deque()
        for task in new_tasks:
            task_manager.add_task(task)


class ContextAgent(BaseAgent):
    def __init__(self, config: Dict, table_name: str, n: int = 5):
        """
        Initialize a ContextAgent instance with its configuration.

        :param config: A dictionary containing agent configuration.
        :param index: The name of the Pinecone index.
        :param n: Number of top tasks to retrieve.
        """
        super().__init__(config)
        self.table_name = table_name
        self.n = n

    def get_relevant_tasks(self, query: str) -> List[str]:
        """
        Retrieve relevant tasks using Pinecone.

        :param query: Input query for context tasks.
        :return: List of relevant tasks.
        """
        query_embedding = get_ada_embedding(query)
        results = self._pinecone_query(query_embedding)

        return self._extract_task_list(results)

    def _pinecone_query(self, query_embedding: List[float]) -> pinecone.FetchResult:
        """
        Perform a Pinecone query.

        :param query_embedding: The query embedding.
        :return: The Pinecone FetchResult object.
        """
        index = pinecone.Index(index_name=self.index)
        return index.query(query_embedding, top_k=self.n, include_metadata=True)

    @staticmethod
    def _extract_task_list(results: pinecone.FetchResult) -> List[str]:
        """
        Extract the task list from the Pinecone FetchResult object.

        :param results: The Pinecone FetchResult object.
        :return: A list of relevant tasks.
        """
        sorted_results = sorted(results.matches, key=lambda x: x.score, reverse=True)
        return [str(item.metadata['task']) for item in sorted_results]

class ExecutionAgent(BaseAgent):
    def __init__(self, config: Dict, context_agent: ContextAgent):
        """
        Initialize an ExecutionAgent instance with its configuration and an instance of ContextAgent.

        :param config: A dictionary containing agent configuration.
        :param context_agent: An instance of the ContextAgent class.
        """
        super().__init__(config)
        self.context_agent = context_agent

    def execute_task(self, objective: str, task: str, n: int = 5) -> str:
        """
        Executes a task based on the given objective and task description.

        :param objective: The overall objective for the AI system.
        :param task: A specific task to be executed by the AI system.
        :param n: Number of top tasks to retrieve. Default is 5.
        :return: A string representing the result of the task execution by the AI agent.
        """
        context = self.context_agent.get_relevant_tasks(query=objective, n=n)
        response_text = self.__call__(objective=objective, task=task, context=context)
        return response_text
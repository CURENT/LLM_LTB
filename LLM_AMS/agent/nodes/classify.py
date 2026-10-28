from datetime import datetime

from agent.schemas.classifier import MessageClassifier
from agent.schemas.response import NodeResponse
from agent.state.app_state import State
from agent.utils.display import display_executing_node


def classify_message(state: State, llm, prompts, schema=MessageClassifier):
    # ``schema``: a MessageClassifier whose routes include registered extensions
    display_executing_node("classifier")

    last_message = state["messages"][-1]
    classifier_llm = llm.with_structured_output(schema)
    result = classifier_llm.invoke([
        {"role": "system", "content": prompts["classifier"]["system"]},
        {"role": "user", "content": last_message.content},
    ])

    nr = NodeResponse(
        node_type="classifier",
        success=True,
        data={"message_type": result.message_type,
              "is_multi_step": result.is_multi_step},
        message=f"Classified as: {result.message_type}"
                + (" (multi-step)" if result.is_multi_step else ""),
        timestamp=datetime.now(),
    )
    return {"message_type": result.message_type,
            "is_multi_step": result.is_multi_step,
            "node_response": nr}

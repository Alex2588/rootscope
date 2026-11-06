# Provisioned declaratively (Terraform in production, infra/rabbitmq/definitions.local.json
# locally and in CI); applications only verify that these exist. A test keeps the local
# definitions and these names in sync.
from app.messaging.contracts import InvestigationExecutionRequested

EXCHANGE = "rootscope.investigations"
QUEUE = "investigations.execute"
DEAD_LETTER_EXCHANGE = "rootscope.investigations.dlx"
DEAD_LETTER_QUEUE = "investigations.execute.dlq"
EXECUTION_ROUTING_KEY = InvestigationExecutionRequested.EVENT_TYPE
DELIVERY_LIMIT = 5

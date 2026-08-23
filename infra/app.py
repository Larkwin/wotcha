import aws_cdk as cdk
from stack import WotchaStack

app = cdk.App()
WotchaStack(app, "Wotcha")
app.synth()

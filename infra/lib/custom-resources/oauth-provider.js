"use strict";

/**
 * Custom Resource Lambda for creating/deleting AgentCore OAuth credential providers.
 * 
 * API shape verified via direct boto3 testing — see research notes.
 * Vendor: CustomOauth2 (Cognito is not a built-in vendor)
 * Response: credentialProviderArn, clientSecretArn.secretArn
 */
const { BedrockAgentCoreControlClient, CreateOauth2CredentialProviderCommand, DeleteOauth2CredentialProviderCommand } = require("@aws-sdk/client-bedrock-agentcore-control");

const client = new BedrockAgentCoreControlClient({});

exports.handler = async (event) => {
  const requestType = event.RequestType;
  const props = event.ResourceProperties;
  console.log("Event:", JSON.stringify({ requestType, props: { ...props, ClientSecret: "***" } }));

  if (requestType === "Create" || requestType === "Update") {
    // On Update, delete existing provider first to avoid conflict (Q-3)
    if (requestType === "Update") {
      try {
        await client.send(new DeleteOauth2CredentialProviderCommand({
          name: props.ProviderName,
        }));
        console.log("Deleted existing provider for update:", props.ProviderName);
      } catch (e) {
        if (e.name !== "ResourceNotFoundException") throw e;
      }
    }

    const resp = await client.send(new CreateOauth2CredentialProviderCommand({
      name: props.ProviderName,
      credentialProviderVendor: "CustomOauth2",
      oauth2ProviderConfigInput: {
        customOauth2ProviderConfig: {
          clientId: props.ClientId,
          clientSecret: props.ClientSecret,
          oauthDiscovery: {
            discoveryUrl: props.DiscoveryUrl,
          },
        },
      },
    }));

    console.log("Created provider:", resp.credentialProviderArn);

    return {
      PhysicalResourceId: props.ProviderName,
      Data: {
        CredentialProviderArn: resp.credentialProviderArn,
        SecretArn: resp.clientSecretArn?.secretArn || "",
      },
    };
  }

  if (requestType === "Delete") {
    try {
      await client.send(new DeleteOauth2CredentialProviderCommand({
        name: event.PhysicalResourceId,
      }));
      console.log("Deleted provider:", event.PhysicalResourceId);
    } catch (e) {
      if (e.name !== "ResourceNotFoundException") throw e;
      console.log("Provider already deleted:", event.PhysicalResourceId);
    }
    return { PhysicalResourceId: event.PhysicalResourceId };
  }

  return { PhysicalResourceId: event.PhysicalResourceId || "unknown" };
};

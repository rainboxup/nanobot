# WeCom Channel (MVP)

This guide explains the current Enterprise WeChat / WeCom channel support in nanobot.

## What this MVP supports

- System-scoped WeCom channel configuration in the admin channel settings
- Outbound text delivery through the WeCom app-message API
- A minimal inbound runtime contract for adapter / bridge integrations

## Current limitations

- No workspace-scoped routing or BYO credentials for WeCom in this MVP
- No built-in callback/webhook ingress server in this slice
- No media/file send support yet; outbound messages are text-only

## Required configuration

Add this to your `config.json`:

```json
{
  "channels": {
    "wecom": {
      "enabled": true,
      "corpId": "ww-your-corp-id",
      "corpSecret": "your-app-secret",
      "agentId": "1000002",
      "allowFrom": ["zhangsan", "lisi"]
    }
  }
}
```

## Field meanings

- `corpId`: WeCom enterprise CorpID
- `corpSecret`: Secret for the custom app used by nanobot
- `agentId`: AgentId of that custom app
- `allowFrom`: Optional allowlist of WeCom user IDs; empty means allow all

## How messages are sent

The MVP uses the WeCom app-message API and sends text messages to the `chat_id` / target user ID passed into the outbound message.

## Recommended rollout scope

Use this MVP when you need:

- owner-managed WeCom access from the admin panel
- basic text delivery to enterprise users
- a narrow starting point before adding richer ingress/media support

If you need callback verification, room routing, or media delivery, treat those as follow-up work rather than assuming they are already included in this MVP.

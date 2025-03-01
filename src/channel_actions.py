from enum import Enum
from typing import Optional, Dict
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

class ChannelAction(str, Enum):
    """Supported actions for channel management."""
    KEEP = "keep"
    ARCHIVE = "archive"
    RENAME = "rename"
    NEW = "new"
    UPDATE_DESCRIPTION = "update_description"
    
    @classmethod
    def values(cls) -> list[str]:
        return [action.value for action in cls]

class ChannelActionResult:
    """Result of a channel action execution."""
    def __init__(self, success: bool, message: str):
        self.success = success
        self.message = message

class ChannelActionHandler:
    def __init__(self, client: WebClient):
        self.client = client
    
    async def execute_action(
        self,
        channel_id: str,
        channel_name: str,
        action: str,
        target_value: Optional[str] = None,
        current_channels: Optional[list] = None
    ) -> ChannelActionResult:
        """
        Execute the specified action on a channel.
        
        Args:
            channel_id: The Slack channel ID
            channel_name: The current channel name
            action: The action to perform (from ChannelAction)
            target_value: Additional value needed for archive (target channel for redirect), 
                         rename (new name), or update_description (new description)
            current_channels: Optional list of already-fetched channels to avoid refetching
        """
        try:
            if action == ChannelAction.KEEP.value:
                return ChannelActionResult(True, f"Channel {channel_name} kept as is")
                
            elif action == ChannelAction.NEW.value:
                return ChannelActionResult(False, f"Channel {channel_name} needs review - please change action from 'new'")
                
            elif action == ChannelAction.ARCHIVE.value:
                response = await self.archive_channel(channel_id, channel_name, target_value, current_channels)
                return response
                
            elif action == ChannelAction.RENAME.value:
                if not target_value:
                    return ChannelActionResult(False, f"New name not specified for renaming {channel_name}")
                response = await self.rename_channel(channel_id, channel_name, target_value)
                return response
                
            elif action == ChannelAction.UPDATE_DESCRIPTION.value:
                if target_value is None or target_value.strip() == "":  # Don't allow empty descriptions
                    return ChannelActionResult(False, f"New description not specified for {channel_name}")
                response = await self.update_description(channel_id, channel_name, target_value)
                return response
                
            else:
                return ChannelActionResult(False, f"Unknown action '{action}' for channel {channel_name}")
                
        except SlackApiError as e:
            error = e.response["error"]
            return ChannelActionResult(False, f"Slack API error for {channel_name}: {error}")
        except Exception as e:
            return ChannelActionResult(False, f"Unexpected error for {channel_name}: {str(e)}")
    
    async def archive_channel(self, channel_id: str, channel_name: str, target_channel: Optional[str] = None, current_channels: Optional[list] = None) -> ChannelActionResult:
        """
        Archive a channel. If target_channel is provided, post a redirect notice before archiving.
        
        Args:
            channel_id: The channel ID to archive
            channel_name: The name of the channel to archive
            target_channel: Optional target channel name for redirect notice
            current_channels: Optional list of already-fetched channels to avoid refetching
        """
        try:
            # First check channel info and status
            try:
                channel_info = self.client.conversations_info(channel=channel_id)["channel"]
                
                # Check if already archived
                if channel_info.get("is_archived", False):
                    return ChannelActionResult(
                        False,
                        f"#{channel_name} is already archived"
                    )
                
                # Check if it's the general channel
                if channel_info.get("is_general"):
                    return ChannelActionResult(
                        False,
                        f"Cannot archive #{channel_name}: This is the workspace's general channel"
                    )
            except SlackApiError as e:
                if e.response["error"] == "channel_not_found":
                    return ChannelActionResult(
                        False,
                        f"Cannot archive #{channel_name}: Channel not found"
                    )
                raise

            # If target channel specified, handle redirect notice
            if target_channel:
                target_channel = target_channel.lstrip('#')
                try:
                    # First check if we already have the channels list
                    target = None
                    
                    if current_channels:
                        # Use the already-fetched channel list
                        target = next((ch for ch in current_channels if ch["name"] == target_channel), None)
                    else:
                        # Fetch channels if we don't have them
                        cursor = None
                        
                        # Paginate through all channels to find the target
                        while True and not target:
                            response = self.client.conversations_list(
                                types="public_channel,private_channel",
                                limit=200,
                                cursor=cursor
                            )
                            
                            channels = response["channels"]
                            found_target = next((ch for ch in channels if ch["name"] == target_channel), None)
                            
                            if found_target:
                                target = found_target
                                break
                            
                            # Check if there are more pages
                            cursor = response.get("response_metadata", {}).get("next_cursor")
                            if not cursor:
                                break
                    
                    if not target:
                        return ChannelActionResult(
                            False,
                            f"Cannot archive #{channel_name}: Target channel #{target_channel} does not exist"
                        )
                    
                    if target.get("is_archived", False):
                        return ChannelActionResult(
                            False,
                            f"Cannot archive #{channel_name}: Target channel #{target_channel} is archived"
                        )
                    
                    # Get the target channel ID for proper mention
                    target_id = target["id"]
                    
                    # Try to post redirect notice
                    message = (
                        f"🔄 *Channel Redirect Notice*\n"
                        f"This channel is being archived. Please join <#{target_id}> to continue the discussion."
                    )
                    
                    try:
                        # Try to join the channel first if we're not already a member
                        try:
                            self.client.conversations_join(channel=channel_id)
                        except SlackApiError:
                            pass  # Ignore if we can't join or are already a member
                        
                        # Try to post the message
                        self.client.chat_postMessage(
                            channel=channel_id,
                            text=message,
                            mrkdwn=True
                        )
                    except SlackApiError as e:
                        if e.response["error"] == "not_in_channel":
                            print(f"\n⚠️  Warning: Could not post redirect notice in #{channel_name} (not a member)")
                            print(f"The channel will be archived without a redirect message")
                            # Continue with archive instead of returning error
                        else:
                            # For other errors, just log a warning and continue
                            print(f"\n⚠️  Warning: Could not post redirect notice in #{channel_name}: {e.response['error']}")
                            print(f"The channel will be archived without a redirect message")
                    
                except SlackApiError:
                    return ChannelActionResult(
                        False,
                        f"Cannot archive #{channel_name}: Failed to verify target channel"
                    )
                
            # Archive the channel
            response = self.client.conversations_archive(channel=channel_id)
            
            if response["ok"]:
                message = f"Successfully archived #{channel_name}"
                if target_channel:
                    message += f" (with redirect to #{target_channel})"
                return ChannelActionResult(True, message)
                
        except SlackApiError as e:
            error = e.response["error"]
            error_messages = {
                "already_archived": f"#{channel_name} is already archived",
                "cant_archive_general": f"Cannot archive #{channel_name}: This is the workspace's general channel",
                "cant_archive_required": f"Cannot archive #{channel_name}: This is a required channel",
                "not_in_channel": f"Cannot archive #{channel_name}: You must be a member of the channel",
                "restricted_action": f"Cannot archive #{channel_name}: Your workspace settings prevent channel archiving",
                "missing_scope": (
                    f"Cannot archive #{channel_name}: Missing required permissions. "
                    "Need channels:write for public channels or groups:write for private channels."
                )
            }
            return ChannelActionResult(
                False, 
                error_messages.get(error, f"Failed to archive #{channel_name}: {error}")
            )
    
    async def rename_channel(
        self,
        channel_id: str,
        old_name: str,
        new_name: str
    ) -> ChannelActionResult:
        """Rename a channel."""
        # Validate new name
        if not new_name:
            return ChannelActionResult(
                False,
                f"Cannot rename {old_name}: New name cannot be empty"
            )
        
        if len(new_name) > 80:
            return ChannelActionResult(
                False,
                f"Cannot rename {old_name}: New name exceeds 80 characters"
            )
        
        # Check for valid characters (lowercase letters, numbers, hyphens, underscores)
        if not all(c.islower() or c.isdigit() or c in '-_' for c in new_name) or '.' in new_name:
            return ChannelActionResult(
                False,
                f"Cannot rename {old_name}: Channel names can only contain lowercase letters, "
                "numbers, hyphens, and underscores"
            )
        
        try:
            response = self.client.conversations_rename(
                channel=channel_id,
                name=new_name
            )
            
            if response["ok"]:
                # Store the actual name returned by Slack as it might be modified
                actual_name = response["channel"]["name"]
                if actual_name != new_name:
                    return ChannelActionResult(
                        True,
                        f"Successfully renamed {old_name} to {actual_name} "
                        "(name was modified to meet Slack's requirements)"
                    )
                return ChannelActionResult(
                    True,
                    f"Successfully renamed {old_name} to {new_name}"
                )
                
        except SlackApiError as e:
            error = e.response["error"]
            error_messages = {
                "not_authorized": "You don't have permission to rename this channel. "
                                "Only channel creators, workspace admins, or channel managers can rename channels.",
                "name_taken": f"Cannot rename {old_name}: The name '{new_name}' is already taken",
                "invalid_name": f"Cannot rename {old_name}: Invalid channel name format",
                "not_in_channel": f"Cannot rename {old_name}: You must be a member of the channel",
                "is_archived": f"Cannot rename {old_name}: Channel is archived"
            }
            return ChannelActionResult(False, error_messages.get(error, f"Failed to rename {old_name}: {error}")) 

    async def update_description(
        self,
        channel_id: str,
        channel_name: str,
        new_description: str
    ) -> ChannelActionResult:
        """Update a channel's description (purpose)."""
        was_member = True  # Track if we were originally a member
        max_retries = 3
        retry_count = 0
        
        try:
            # Check if channel exists and is not archived
            try:
                channel_info = self.client.conversations_info(channel=channel_id)["channel"]
                
                # Check if already archived
                if channel_info.get("is_archived", False):
                    return ChannelActionResult(
                        False,
                        f"Cannot update description for #{channel_name}: Channel is archived"
                    )
                
                # Check if we're a member directly from the API response
                was_member = channel_info.get("is_member", False)
            except SlackApiError as e:
                if e.response["error"] == "channel_not_found":
                    return ChannelActionResult(
                        False,
                        f"Cannot update description for #{channel_name}: Channel not found"
                    )
                raise
            
            # Try to join the channel if we're not a member
            if not was_member:
                try:
                    print(f"Joining channel #{channel_name} to update description...")
                    join_response = self.client.conversations_join(channel=channel_id)
                    if not join_response.get("ok", False):
                        return ChannelActionResult(
                            False,
                            f"Cannot update description for #{channel_name}: Failed to join channel"
                        )
                except SlackApiError as e:
                    error = e.response["error"]
                    if error in ["method_not_supported_for_channel_type", "channel_not_found", "missing_scope"]:
                        return ChannelActionResult(
                            False,
                            f"Cannot update description for #{channel_name}: Not a member and unable to join ({error})"
                        )
                    # For other errors, log and continue
                    print(f"Warning: Error joining #{channel_name}: {error}")
            
            # Update the channel purpose with retry logic for rate limits
            while retry_count < max_retries:
                try:
                    response = self.client.conversations_setPurpose(
                        channel=channel_id,
                        purpose=new_description
                    )
                    
                    # Leave the channel if we weren't originally a member
                    if not was_member:
                        try:
                            print(f"Leaving channel #{channel_name} after updating description...")
                            self.client.conversations_leave(channel=channel_id)
                        except SlackApiError as leave_error:
                            # If we can't leave, just log and continue
                            print(f"Warning: Could not leave channel #{channel_name}: {leave_error.response.get('error', 'unknown error')}")
                    
                    if response["ok"]:
                        return ChannelActionResult(
                            True,
                            f"Successfully updated description for #{channel_name}"
                        )
                    
                except SlackApiError as e:
                    error = e.response["error"]
                    
                    # Handle rate limiting
                    if error == "ratelimited":
                        retry_count += 1
                        retry_after = int(e.response.get("headers", {}).get("Retry-After", 5))
                        print(f"Rate limited when updating #{channel_name}, waiting {retry_after}s (retry {retry_count}/{max_retries})")
                        
                        # Wait before retrying
                        import asyncio
                        await asyncio.sleep(retry_after)
                        continue
                    
                    # Handle other errors
                    error_messages = {
                        "not_in_channel": f"Cannot update description for #{channel_name}: You must be a member of the channel",
                        "is_archived": f"Cannot update description for #{channel_name}: Channel is archived",
                        "missing_scope": f"Cannot update description for #{channel_name}: Missing required permissions",
                        "not_authorized": f"Cannot update description for #{channel_name}: You don't have permission to update the description"
                    }
                    
                    # Leave the channel if we joined but couldn't update
                    if not was_member:
                        try:
                            print(f"Leaving channel #{channel_name} after failed update attempt...")
                            self.client.conversations_leave(channel=channel_id)
                        except SlackApiError:
                            pass
                    
                    return ChannelActionResult(
                        False, 
                        error_messages.get(error, f"Failed to update description for #{channel_name}: {error}")
                    )
                
                # If we got here, we succeeded
                break
            
            # If we've exhausted retries
            if retry_count >= max_retries:
                # Leave the channel if we joined but couldn't update due to rate limits
                if not was_member:
                    try:
                        print(f"Leaving channel #{channel_name} after exhausting retries...")
                        self.client.conversations_leave(channel=channel_id)
                    except SlackApiError:
                        pass
                
                return ChannelActionResult(
                    False,
                    f"Failed to update description for #{channel_name}: Rate limit exceeded after {max_retries} retries"
                )
                
        except Exception as e:
            # For any other unexpected errors
            # Leave the channel if we joined but encountered an error
            if not was_member:
                try:
                    print(f"Leaving channel #{channel_name} after error...")
                    self.client.conversations_leave(channel=channel_id)
                except Exception:
                    pass
            
            return ChannelActionResult(
                False,
                f"Unexpected error updating description for #{channel_name}: {str(e)}"
            ) 
"""Source-declared workflow control request generation."""

from workflow_container_contract import (
    WorkflowControlFinalRequest,
    WorkflowControlManifestRequest,
    WorkflowControlSafepointRequest,
    WorkflowDefinition,
    WorkflowResult,
)


class WorkflowControlRequestBuilder:
    """Build control requests that conform to one exact source definition."""

    def __init__(self, *, workflow_definition: WorkflowDefinition) -> None:
        """Store the immutable source declaration used by this image.

        Args:
            workflow_definition: Validated exact `workflow.yaml` declaration.
        """

        self._workflow_definition = workflow_definition

    def final_build(
        self,
        *,
        manifest_request_list: list[WorkflowControlManifestRequest],
        transition_identity: str,
        workflow_result: WorkflowResult,
    ) -> WorkflowControlFinalRequest:
        """Build one final request after resolving every declared manifest.

        Args:
            manifest_request_list: Canonically ordered requested manifest instances.
            transition_identity: Stable final transition identity.
            workflow_result: Exact open workflow result.

        Returns:
            Validated final request.
        """

        self._manifest_request_list_validate(manifest_request_list)
        return WorkflowControlFinalRequest(
            manifest_request_list=manifest_request_list,
            transition_identity=transition_identity,
            workflow_result=workflow_result,
        )

    def manifest_build(
        self,
        *,
        manifest_key: str,
        path_parameter_by_name_map: dict[str, str],
    ) -> WorkflowControlManifestRequest:
        """Build and resolve one manifest request before platform submission.

        Args:
            manifest_key: Stable source-declared run manifest key.
            path_parameter_by_name_map: Exact safe template parameter values.

        Returns:
            Validated manifest request accepted by the source declaration.

        Raises:
            ValueError: If the source has no Data declaration or the request does not resolve exactly.
        """

        request = WorkflowControlManifestRequest(
            manifest_key=manifest_key,
            path_parameter_by_name_map=path_parameter_by_name_map,
        )
        self.manifest_path_get(request)
        return request

    def manifest_path_get(self, request: WorkflowControlManifestRequest) -> str:
        """Return the resolved image-visible path for one validated request.

        Args:
            request: Exact manifest request.

        Returns:
            Canonical path beginning with `result/` or `workspace/`.

        Raises:
            ValueError: If the source has no matching run declaration.
        """

        if self._workflow_definition.data is None:
            raise ValueError("workflow source does not declare run manifests")
        return self._workflow_definition.data.run_manifest_path_get(request)

    def safepoint_build(
        self,
        *,
        manifest_request_list: list[WorkflowControlManifestRequest],
        step_identity: str,
        step_key: str,
        transition_identity: str,
    ) -> WorkflowControlSafepointRequest:
        """Build one safepoint bound to a declared source step and exact manifests.

        Args:
            manifest_request_list: Canonically ordered requested manifest instances.
            step_identity: Stable current step-instance identity.
            step_key: Source-declared step key that owns platform policy.
            transition_identity: Stable completion transition identity.

        Returns:
            Validated safepoint request.

        Raises:
            ValueError: If the step key or one manifest is undeclared.
        """

        if step_key not in self._workflow_definition.step_by_key_map:
            raise ValueError("safepoints must reference declared workflow steps")
        self._manifest_request_list_validate(manifest_request_list)
        return WorkflowControlSafepointRequest(
            manifest_request_list=manifest_request_list,
            step_identity=step_identity,
            step_key=step_key,
            transition_identity=transition_identity,
        )

    def _manifest_request_list_validate(
        self,
        manifest_request_list: list[WorkflowControlManifestRequest],
    ) -> None:
        """Resolve every requested manifest against the exact source declaration.

        Args:
            manifest_request_list: Requested manifest instances.
        """

        for manifest_request in manifest_request_list:
            self.manifest_path_get(manifest_request)

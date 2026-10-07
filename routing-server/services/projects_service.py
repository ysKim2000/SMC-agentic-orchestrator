class ProjectsService:
    def __init__(self, projects_repo):
        self.projects_repo = projects_repo

    async def get_departments(self):
        departments = await self.projects_repo.get_departments()
        return [d["department_name"] for d in departments]

    async def get_projects_by_department(self, department_name: str):
        projects = await self.projects_repo.get_projects_by_department(department_name)
        if not projects:
            return None
        names = [pv for pv in projects.values()]
        return names, projects

    async def get_models_by_projects_by_department(self, department_name: str, project_name: str):
        """project_name으로 모델 정보 조회 (project_number 대신 project_name 사용)."""
        result = await self.projects_repo.get_models_by_department_and_project(department_name, project_name)
        if "error" in result:
            return None
        models = result.get("models", {})
        return list(models.values()), models

    async def get_data_by_model(self, department_name: str, project_name: str):
        """모델의 required_data 반환."""
        pv = await self.projects_repo.get_model_by_project(department_name, project_name)
        if not pv:
            return None
        return pv.get("required_data", [])

# How to Run the Project (Windows & Linux)

This guide provides instructions to set up and run the AI Server (Backend) and the Next.js application (Frontend) on both Windows and Linux environments.

## Prerequisites

* **Docker & Docker Compose**: Required for the backend services.
    * *Windows*: Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
    * *Linux*: Install Docker Engine and the Docker Compose plugin.
* **Node.js & Package Manager**: Required for the Next.js frontend. Install Node.js and your preferred package manager (npm, yarn, pnpm, or bun).

## 1. Environment Setup (Both OS)

Before running the application, you must configure your environment variables:

1.  In the root directory of the project, locate the `.env.example` file.
2.  Create a copy of this file and rename it to `.env`.
3.  Open `.env` and fill in your required API keys and database connection string:
    ```env
    OPENAI_API_KEY=your_key_here
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_HOST="[https://cloud.langfuse.com](https://cloud.langfuse.com)"
    DATABASE_URL=postgresql://postgres.[your-project]:[password]@aws-0-[region][.pooler.supabase.com:6543/postgres](https://.pooler.supabase.com:6543/postgres)
    ```

## 2. Running the Backend (AI Server)

The backend runs inside a Docker container using the `api` service and exposes port `8000`. 

### On Linux (Using Makefile)

Linux systems natively support `make` utilities. You can use the provided `Makefile` to manage the Docker containers:

* **Start the server:** `make up` (Runs `docker compose up -d`)
* **View live logs:** `make logs`
* **Stop the server:** `make down`
* **Rebuild the Docker image:** `make build`
* **Access the container shell:** `make shell`

### On Windows

Windows does not include the `make` utility by default in PowerShell or Command Prompt. You should run the underlying Docker Compose commands directly:

* **Start the server:** `docker compose up -d`
* **View live logs:** `docker compose logs -f api`
* **Stop the server:** `docker compose down`
* **Rebuild the Docker image:** `docker compose build`
* **Access the container shell:** `docker exec -it vinuni-ai-agent bash`

*(Tip: If you are using Windows Subsystem for Linux (WSL2) or Git Bash, you can use the Linux `make` commands).*

## 3. Running the Frontend (Next.js)

The steps to run the frontend are identical for both Windows and Linux.

1.  Open a new terminal or command prompt window.
2.  Navigate to the frontend directory:
    ```bash
    cd frontend
    ```
3.  Install the required dependencies:
    ```bash
    npm install
    ```
    *(Note: You can also use `yarn`, `pnpm`, or `bun`)*
4.  Start the Next.js development server:
    ```bash
    npm run dev
    ```
5.  Open your web browser and navigate to [http://localhost:3000](http://localhost:3000) to view the application.

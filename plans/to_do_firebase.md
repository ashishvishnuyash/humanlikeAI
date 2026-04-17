firestore.indexes.json created at the project root with 6 indexes:

  ┌────────────┬─────────────────────────────────────┬──────────────────────────────────────────┐
  │ Collection │                Index                │                 Used by                  │                                                             
  ├────────────┼─────────────────────────────────────┼──────────────────────────────────────────┤                                                               
  │ users      │ company_id + is_active              │ list_employees server-side active filter │
  ├────────────┼─────────────────────────────────────┼──────────────────────────────────────────┤                                                               
  │ users      │ company_id + is_active + department │ list_employees with department filter    │                                                             
  ├────────────┼─────────────────────────────────────┼──────────────────────────────────────────┤                                                             
  │ check_ins  │ company_id + created_at DESC        │ employer_dashboard check-in fetch        │
  ├────────────┼─────────────────────────────────────┼──────────────────────────────────────────┤
  │ check_ins  │ user_id + created_at DESC           │ employee activity summary                │
  ├────────────┼─────────────────────────────────────┼──────────────────────────────────────────┤
  │ sessions   │ company_id + created_at DESC        │ employer_dashboard session fetch         │
  ├────────────┼─────────────────────────────────────┼──────────────────────────────────────────┤
  │ sessions   │ user_id + created_at DESC           │ employee activity summary                │
  └────────────┴─────────────────────────────────────┴──────────────────────────────────────────┘

  To deploy to Firebase, run:
  firebase deploy --only firestore:indexes

  This requires the Firebase CLI (npm install -g firebase-tools) and that you're logged in (firebase login). The firebaseadmn.json in your project suggests the
  project is already set up — you may also need a firebase.json pointing at this file if you don't have one yet.

  Alternatively, if you don't use the CLI, when the endpoint first gets called without the index, Firebase returns a 400 error with a direct link in the error
  message — clicking it opens the Firebase console and creates the index in one click.
